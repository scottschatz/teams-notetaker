"""
Microsoft Graph API Client

Provides authenticated access to Microsoft Graph API using MSAL (Microsoft Authentication Library).
Handles token acquisition, auto-refresh, rate limiting, and retry logic.
"""

import logging
import time
from typing import Any, Dict, Optional, List
from datetime import datetime, timedelta
import requests
from msal import ConfidentialClientApplication

from ..core.config import GraphAPIConfig
from ..core.exceptions import GraphAPIError, AuthenticationError, RateLimitError


logger = logging.getLogger(__name__)


class GraphAPIClient:
    """
    Microsoft Graph API client with MSAL authentication.

    Supports:
    - Client credentials flow (application permissions)
    - Automatic token refresh on 401 responses
    - Retry logic with exponential backoff
    - Rate limit handling (429 responses with Retry-After)

    Usage:
        config = GraphAPIConfig(client_id='...', client_secret='...', tenant_id='...')
        client = GraphAPIClient(config)
        response = client.get('/users')
    """

    BASE_URL = "https://graph.microsoft.com/v1.0"
    BETA_URL = "https://graph.microsoft.com/beta"
    SCOPES = ["https://graph.microsoft.com/.default"]

    def __init__(self, config: GraphAPIConfig, use_beta: bool = False):
        """
        Initialize Graph API client.

        Args:
            config: GraphAPIConfig with client credentials
            use_beta: If True, use beta endpoint instead of v1.0
        """
        self.config = config
        self.base_url = self.BETA_URL if use_beta else self.BASE_URL
        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None

        # Initialize MSAL confidential client
        self._msal_client = ConfidentialClientApplication(
            client_id=config.client_id,
            client_credential=config.client_secret,
            authority=f"https://login.microsoftonline.com/{config.tenant_id}"
        )

        logger.info(f"GraphAPIClient initialized (tenant: {config.tenant_id[:8]}..., beta: {use_beta})")

    def _authenticate(self) -> str:
        """
        Acquire access token using client credentials flow.

        Returns:
            Access token string

        Raises:
            AuthenticationError: If authentication fails
        """
        try:
            # Check if we have a valid cached token
            if self._access_token and self._token_expires_at:
                if datetime.now() < self._token_expires_at - timedelta(minutes=5):
                    logger.debug("Using cached access token")
                    return self._access_token

            # Acquire new token
            logger.info("Acquiring new access token from Microsoft Identity Platform")
            result = self._msal_client.acquire_token_for_client(scopes=self.SCOPES)

            if "access_token" not in result:
                error_desc = result.get("error_description", result.get("error", "Unknown error"))
                raise AuthenticationError(f"Failed to acquire token: {error_desc}")

            # Cache token with expiration
            self._access_token = result["access_token"]
            expires_in = result.get("expires_in", 3600)  # Default 1 hour
            self._token_expires_at = datetime.now() + timedelta(seconds=expires_in)

            logger.info(f"Access token acquired successfully (expires in {expires_in}s)")
            return self._access_token

        except Exception as e:
            logger.error(f"Authentication failed: {e}", exc_info=True)
            raise AuthenticationError(f"Graph API authentication failed: {e}")

    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        data: Optional[Any] = None,
        headers: Optional[Dict[str, str]] = None,
        retry_count: int = 0,
        max_retries: int = 3
    ) -> requests.Response:
        """
        Make authenticated request to Graph API with retry logic.

        Args:
            method: HTTP method (GET, POST, PUT, PATCH, DELETE)
            endpoint: API endpoint (e.g., '/users' or full URL)
            params: Query parameters
            json: JSON body (for POST/PATCH)
            data: Raw body data
            headers: Additional headers
            retry_count: Current retry attempt (internal)
            max_retries: Maximum retry attempts

        Returns:
            requests.Response object

        Raises:
            GraphAPIError: If request fails after retries
            RateLimitError: If rate limited and max retries exceeded
            AuthenticationError: If authentication fails
        """
        # Get access token
        token = self._authenticate()

        # Build full URL
        if endpoint.startswith("http"):
            url = endpoint
        else:
            url = f"{self.base_url}{endpoint}" if endpoint.startswith("/") else f"{self.base_url}/{endpoint}"

        # Prepare headers
        request_headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        if headers:
            request_headers.update(headers)

        try:
            logger.debug(f"{method} {url} (retry {retry_count}/{max_retries})")

            # Make request
            response = requests.request(
                method=method,
                url=url,
                params=params,
                json=json,
                data=data,
                headers=request_headers,
                timeout=30
            )

            # Handle rate limiting (429)
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 60))

                if retry_count < max_retries:
                    logger.warning(f"Rate limited (429), waiting {retry_after}s before retry {retry_count + 1}/{max_retries}")
                    time.sleep(retry_after)
                    return self._request(method, endpoint, params, json, data, headers, retry_count + 1, max_retries)
                else:
                    raise RateLimitError(f"Rate limit exceeded after {max_retries} retries")

            # Handle authentication errors (401)
            if response.status_code == 401:
                # Log the actual error message from Microsoft
                try:
                    error_data = response.json()
                    error_msg = error_data.get("error", {}).get("message", response.text)
                    logger.error(f"401 Unauthorized: {error_msg}")
                except:
                    logger.error(f"401 Unauthorized: {response.text}")

                if retry_count < max_retries:
                    logger.warning(f"Authentication failed (401), refreshing token and retrying {retry_count + 1}/{max_retries}")
                    self._access_token = None  # Force token refresh
                    self._token_expires_at = None
                    return self._request(method, endpoint, params, json, data, headers, retry_count + 1, max_retries)
                else:
                    raise AuthenticationError(f"Authentication failed after {max_retries} retries: {error_msg if 'error_msg' in locals() else response.text}")

            # Handle server errors (500-599) with exponential backoff
            if 500 <= response.status_code < 600:
                if retry_count < max_retries:
                    wait_time = min(2 ** retry_count, 30)  # Exponential backoff, max 30s
                    logger.warning(f"Server error ({response.status_code}), waiting {wait_time}s before retry {retry_count + 1}/{max_retries}")
                    time.sleep(wait_time)
                    return self._request(method, endpoint, params, json, data, headers, retry_count + 1, max_retries)
                else:
                    raise GraphAPIError(f"Server error after {max_retries} retries: {response.status_code} {response.text}")

            # Handle client errors (400-499, except 401 and 429 handled above)
            if 400 <= response.status_code < 500:
                error_msg = f"Graph API request failed: {response.status_code}"
                try:
                    error_data = response.json()
                    error_detail = error_data.get("error", {}).get("message", response.text)
                    error_msg = f"{error_msg} - {error_detail}"
                except:
                    error_msg = f"{error_msg} - {response.text}"

                logger.error(f"{method} {url} failed: {error_msg}")
                raise GraphAPIError(error_msg)

            # Success
            response.raise_for_status()
            return response

        except (requests.RequestException, GraphAPIError, AuthenticationError, RateLimitError):
            raise
        except Exception as e:
            logger.error(f"Unexpected error in Graph API request: {e}", exc_info=True)
            raise GraphAPIError(f"Unexpected error: {e}")

    def get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        GET request to Graph API.

        Args:
            endpoint: API endpoint
            params: Query parameters

        Returns:
            JSON response as dictionary
        """
        response = self._request("GET", endpoint, params=params)
        return response.json()

    def get_text(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> str:
        """
        GET request to Graph API that returns text content.

        Args:
            endpoint: API endpoint
            params: Query parameters

        Returns:
            Response body as text string
        """
        response = self._request("GET", endpoint, params=params)
        return response.text

    def post(self, endpoint: str, json: Optional[Dict[str, Any]] = None, data: Optional[Any] = None) -> Dict[str, Any]:
        """
        POST request to Graph API.

        Args:
            endpoint: API endpoint
            json: JSON body
            data: Raw body data

        Returns:
            JSON response as dictionary
        """
        response = self._request("POST", endpoint, json=json, data=data)
        return response.json() if response.content else {}

    def patch(self, endpoint: str, json: Dict[str, Any]) -> Dict[str, Any]:
        """
        PATCH request to Graph API.

        Args:
            endpoint: API endpoint
            json: JSON body with fields to update

        Returns:
            JSON response as dictionary
        """
        response = self._request("PATCH", endpoint, json=json)
        return response.json() if response.content else {}

    def delete(self, endpoint: str) -> bool:
        """
        DELETE request to Graph API.

        Args:
            endpoint: API endpoint

        Returns:
            True if successful
        """
        response = self._request("DELETE", endpoint)
        return response.status_code == 204

    def get_paged(self, endpoint: str, params: Optional[Dict[str, Any]] = None, max_pages: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        GET request with automatic pagination support.

        Graph API uses @odata.nextLink for pagination. This method automatically
        follows pagination links and returns all results.

        Args:
            endpoint: API endpoint
            params: Query parameters
            max_pages: Maximum number of pages to fetch (None = all pages)

        Returns:
            List of all items from all pages
        """
        all_items = []
        page_count = 0
        next_link = endpoint

        while next_link:
            # Check max_pages limit
            if max_pages and page_count >= max_pages:
                logger.info(f"Reached max_pages limit ({max_pages})")
                break

            # Get page
            if page_count == 0:
                response = self.get(next_link, params=params)
            else:
                # For subsequent pages, use the @odata.nextLink directly (already includes params)
                response = self.get(next_link)

            # Extract items
            items = response.get("value", [])
            all_items.extend(items)
            page_count += 1

            logger.debug(f"Fetched page {page_count}, got {len(items)} items (total: {len(all_items)})")

            # Check for next page
            next_link = response.get("@odata.nextLink")

        logger.info(f"Pagination complete: {page_count} pages, {len(all_items)} total items")
        return all_items

    def batch_get(self, requests: List[Dict[str, str]], max_batch_size: int = 20) -> List[Dict[str, Any]]:
        """
        Execute multiple GET requests in a single batch call.

        Graph API /$batch endpoint allows up to 20 requests per batch.
        This method handles splitting larger request lists into multiple batches.

        Args:
            requests: List of dicts with 'id' and 'url' keys
                      Example: [{"id": "1", "url": "/users/user1@example.com"}, ...]
            max_batch_size: Maximum requests per batch (default 20, Graph API limit)

        Returns:
            List of response dicts with 'id', 'status', and 'body' keys
            Results are returned in the same order as requests

        Usage:
            requests = [
                {"id": "user1", "url": "/users/user1@example.com"},
                {"id": "user2", "url": "/users/user2@example.com"},
            ]
            responses = client.batch_get(requests)
            for resp in responses:
                if resp["status"] == 200:
                    user_data = resp["body"]
        """
        if not requests:
            return []

        all_responses = []

        # Process in batches of max_batch_size
        for i in range(0, len(requests), max_batch_size):
            batch = requests[i:i + max_batch_size]

            # Build batch payload
            batch_payload = {
                "requests": [
                    {
                        "id": req["id"],
                        "method": "GET",
                        "url": req["url"] if req["url"].startswith("/") else f"/{req['url']}"
                    }
                    for req in batch
                ]
            }

            try:
                logger.debug(f"Executing batch request with {len(batch)} items")
                response = self.post("/$batch", json=batch_payload)

                # Extract responses
                batch_responses = response.get("responses", [])

                # Create response lookup by ID
                response_by_id = {r["id"]: r for r in batch_responses}

                # Return in original request order
                for req in batch:
                    req_id = req["id"]
                    if req_id in response_by_id:
                        resp = response_by_id[req_id]
                        all_responses.append({
                            "id": req_id,
                            "status": resp.get("status", 500),
                            "body": resp.get("body", {})
                        })
                    else:
                        logger.warning(f"No response for batch request ID {req_id}")
                        all_responses.append({
                            "id": req_id,
                            "status": 500,
                            "body": {"error": {"message": "No response in batch"}}
                        })

                logger.debug(f"Batch complete: {len(batch_responses)} responses")

            except Exception as e:
                logger.error(f"Batch request failed: {e}")
                # Return error responses for all items in this batch
                for req in batch:
                    all_responses.append({
                        "id": req["id"],
                        "status": 500,
                        "body": {"error": {"message": str(e)}}
                    })

        logger.info(f"Batch GET complete: {len(all_responses)} total responses from {len(requests)} requests")
        return all_responses

    def get_user_photo(self, user_id: str, size: str = "48x48") -> Optional[str]:
        """
        Get user profile photo as base64-encoded string.

        Args:
            user_id: User ID or email address
            size: Photo size (48x48, 64x64, 96x96, 120x120, 240x240, etc.)

        Returns:
            Base64-encoded photo string, or None if photo not available
        """
        try:
            endpoint = f"/users/{user_id}/photos/{size}/$value"

            # Make request with custom headers to get binary data
            response = self._request("GET", endpoint)

            if response.status_code == 200:
                import base64
                photo_base64 = base64.b64encode(response.content).decode('utf-8')
                logger.debug(f"Fetched {size} photo for user {user_id}")
                return photo_base64
            else:
                logger.debug(f"No photo available for user {user_id}")
                return None

        except Exception as e:
            logger.debug(f"Could not fetch photo for {user_id}: {e}")
            return None

    def get_user_details(self, user_id: str) -> Dict[str, Any]:
        """
        Get user details including job title, department, etc.

        Args:
            user_id: User ID or email address

        Returns:
            Dictionary with user details (displayName, jobTitle, mail, etc.)
        """
        try:
            endpoint = f"/users/{user_id}"
            params = {"$select": "displayName,mail,userPrincipalName,jobTitle,department,officeLocation"}

            user_data = self.get(endpoint, params=params)
            logger.debug(f"Fetched details for user {user_id}: {user_data.get('displayName')}")
            return user_data

        except Exception as e:
            logger.warning(f"Could not fetch details for {user_id}: {e}")
            return {}

    def enrich_user_with_photo_and_title(self, user_email: str, display_name: str) -> Dict[str, Any]:
        """
        Enrich user data with profile photo and job title.
        Tries alternate email formats for aliases (e.g., Scott.Schatz -> sschatz).

        Args:
            user_email: User email address
            display_name: User display name

        Returns:
            Dictionary with displayName, email, jobTitle, photo_base64
        """
        enriched = {
            "displayName": display_name,
            "email": user_email,
            "jobTitle": None,
            "photo_base64": None
        }

        # Generate alternate email formats to try (same logic as meetings.py)
        normalized_email = user_email.lower()
        alternate_emails = [normalized_email]

        if '.' in normalized_email.split('@')[0]:
            # Try without the dot: "scott.schatz@domain.com" -> "sschatz@domain.com"
            local_part, domain = normalized_email.split('@')
            parts = local_part.split('.')
            if len(parts) == 2:
                # Take first letter of first name + last name
                alternate = f"{parts[0][0]}{parts[1]}@{domain}"
                alternate_emails.append(alternate)

        # Try each email format
        for attempt_email in alternate_emails:
            try:
                # Get user details (job title, etc.)
                details = self.get_user_details(attempt_email)
                if details:
                    enriched["jobTitle"] = details.get("jobTitle")
                    enriched["department"] = details.get("department")

                    # Get profile photo (48x48 thumbnail)
                    photo = self.get_user_photo(attempt_email, size="48x48")
                    if photo:
                        enriched["photo_base64"] = photo

                    if attempt_email != normalized_email:
                        logger.debug(f"Found details for {user_email} using alternate format: {attempt_email}")

                    # Success - stop trying other formats
                    break

            except Exception as e:
                # Try next format
                if attempt_email == alternate_emails[-1]:
                    # Last attempt failed
                    logger.debug(f"Could not enrich user {user_email} (tried {len(alternate_emails)} formats): {e}")
                continue

        return enriched

    def test_connection(self) -> bool:
        """
        Test Graph API connection by fetching organization info.

        Returns:
            True if connection successful

        Raises:
            GraphAPIError: If connection fails
        """
        try:
            logger.info("Testing Graph API connection...")
            result = self.get("/organization")
            org_name = result.get("value", [{}])[0].get("displayName", "Unknown")
            logger.info(f"✓ Graph API connection successful (org: {org_name})")
            return True
        except Exception as e:
            logger.error(f"✗ Graph API connection failed: {e}")
            raise GraphAPIError(f"Connection test failed: {e}")
