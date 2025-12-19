"""
Azure Relay Hybrid Connection Listener for Microsoft Graph Webhooks.

Receives webhook notifications from Microsoft Graph via Azure Relay,
allowing WSL-based services to receive webhooks without public exposure.
"""

import asyncio
import aiohttp
import hmac
import hashlib
import base64
import json
import logging
from typing import Callable, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class AzureRelayWebhookListener:
    """
    Listens for Microsoft Graph webhooks via Azure Relay Hybrid Connection.

    Azure Relay provides secure inbound connectivity to WSL without
    exposing ports publicly.

    Usage:
        listener = AzureRelayWebhookListener(
            relay_namespace="myrelay.servicebus.windows.net",
            hybrid_connection_name="webhooks",
            shared_access_key_name="RootManageSharedAccessKey",
            shared_access_key="<key>"
        )

        await listener.start(callback=handle_webhook)
    """

    def __init__(
        self,
        relay_namespace: str,
        hybrid_connection_name: str,
        shared_access_key_name: str,
        shared_access_key: str
    ):
        """
        Initialize Azure Relay listener.

        Args:
            relay_namespace: Relay namespace (e.g., "myrelay.servicebus.windows.net")
            hybrid_connection_name: Name of hybrid connection (e.g., "webhooks")
            shared_access_key_name: SAS key name (usually "RootManageSharedAccessKey")
            shared_access_key: SAS key value
        """
        self.relay_namespace = relay_namespace
        self.hybrid_connection_name = hybrid_connection_name
        self.shared_access_key_name = shared_access_key_name
        self.shared_access_key = shared_access_key

        self.callback: Optional[Callable] = None
        self.running = False
        self._session: Optional[aiohttp.ClientSession] = None  # Reusable session for rendezvous

    def _generate_sas_token(self, uri: str, expiry_seconds: int = 3600) -> str:
        """
        Generate Shared Access Signature token (Microsoft example format).

        Args:
            uri: Resource URI (e.g., "http://namespace/path")
            expiry_seconds: Token validity period in seconds

        Returns:
            SAS token string
        """
        import urllib.parse

        # URL-encode the resource URI
        encoded_resource_uri = urllib.parse.quote(uri, safe='')

        # Calculate expiry timestamp
        expiry = int(datetime.utcnow().timestamp()) + expiry_seconds

        # Create signature string: encoded_uri + "\n" + expiry
        plain_signature = f"{encoded_resource_uri}\n{expiry}"

        # Generate HMAC-SHA256 hash
        key_bytes = self.shared_access_key.encode('utf-8')
        signature_bytes = plain_signature.encode('utf-8')
        hash_bytes = hmac.new(key_bytes, signature_bytes, hashlib.sha256).digest()
        base64_hash = base64.b64encode(hash_bytes)

        # Build SAS token (Microsoft format)
        token = (f"SharedAccessSignature sr={encoded_resource_uri}"
                f"&sig={urllib.parse.quote(base64_hash)}"
                f"&se={expiry}"
                f"&skn={self.shared_access_key_name}")
        return token

    async def start(self, callback: Callable):
        """
        Start listening for webhook notifications.

        Args:
            callback: Async function to call when webhook received
                     Signature: async def callback(notification: dict) -> dict
        """
        self.callback = callback
        self.running = True

        # Generate SAS token (Microsoft example format)
        # Resource URI does NOT include /$hc/ path
        resource_uri = f"http://{self.relay_namespace}/{self.hybrid_connection_name}"
        sas_token = self._generate_sas_token(resource_uri)

        # Build WebSocket URL (Microsoft example format)
        # Requires: sb-hc-action=listen and sb-hc-id parameters
        from urllib.parse import quote
        ws_url = (f"wss://{self.relay_namespace}/$hc/{self.hybrid_connection_name}"
                 f"?sb-hc-action=listen&sb-hc-id=listener-{id(self)}"
                 f"&sb-hc-token={quote(sas_token)}")

        logger.info(f"Starting Azure Relay listener on wss://{self.relay_namespace}/$hc/{self.hybrid_connection_name}")

        # Create reusable session for fast rendezvous connections
        connector = aiohttp.TCPConnector(
            limit=10,  # Connection pool size
            ttl_dns_cache=300,  # Cache DNS for 5 minutes
            keepalive_timeout=30  # Keep connections warm
        )
        self._session = aiohttp.ClientSession(connector=connector)

        try:
            while self.running:
                try:
                    async with self._session.ws_connect(ws_url) as ws:
                        logger.info("✅ Connected to Azure Relay")

                        # Accept incoming HTTP requests over WebSocket
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                await self._handle_message(msg.data, ws)
                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                logger.error(f"WebSocket error: {ws.exception()}")
                                break

                except Exception as e:
                    logger.error(f"Azure Relay connection error: {e}")
                    if self.running:
                        logger.info("Reconnecting in 5 seconds...")
                        await asyncio.sleep(5)
        finally:
            if self._session:
                await self._session.close()

    async def _handle_message(self, data: str, ws):
        """
        Handle incoming HTTP request over WebSocket.

        Azure Relay sends HTTP requests as JSON over WebSocket.
        """
        try:
            request = json.loads(data)

            # Only log full structure at debug level to reduce overhead
            logger.debug(f"Full request structure: {json.dumps(request, indent=2)}")

            # Extract HTTP request details from correct structure
            req_details = request.get("request", {})
            method = req_details.get("method", "")
            request_target = req_details.get("requestTarget", "")
            headers = req_details.get("requestHeaders", {})
            body = req_details.get("body")
            request_id = req_details.get("id", "unknown")

            # Parse path and query parameters from requestTarget
            if "?" in request_target:
                path, query_string = request_target.split("?", 1)
            else:
                path = request_target
                query_string = ""

            logger.debug(f"Received {method} {path} (requestId: {request_id})")
            logger.debug(f"Query string: {query_string}")

            # Log body safely at debug level (body can be str, bool, or None)
            if isinstance(body, str):
                logger.debug(f"Body type: str, Body content: {body[:200]}")
            elif isinstance(body, bool):
                logger.debug(f"Body type: bool, Body value: {body}")
            else:
                logger.debug(f"Body type: {type(body)}, Body content: None/Empty")

            # Check for validation token in query parameters (Microsoft Graph sends it there)
            # FAST PATH: Prioritize validation handling for speed
            import urllib.parse
            if "validationToken=" in query_string:
                query_params = urllib.parse.parse_qs(query_string)
                validation_token = query_params.get("validationToken", [None])[0]
                if validation_token:
                    import time
                    start_time = time.time()
                    logger.info(f"⚡ Validation request received (requestId: {request_id[:20]}...)")

                    # Check if this is a rendezvous request (sb-hc-action=request in address)
                    address = req_details.get("address", "")
                    if "sb-hc-action=request" in address:
                        # Must connect to the rendezvous address and send response there
                        await self._send_rendezvous_response(address, request_id, validation_token)
                        elapsed = time.time() - start_time
                        logger.info(f"✅ Validation complete in {elapsed:.3f}s")
                    else:
                        # Small request, send response on control channel
                        logger.info(f"Sending validation response on control channel (requestId={request_id})")
                        response = {
                            "response": {
                                "requestId": request_id,
                                "statusCode": "200",
                                "statusDescription": "OK",
                                "responseHeaders": {
                                    "Content-Type": "text/plain; charset=utf-8"
                                },
                                "body": True  # Tells Azure Relay to expect binary body frames
                            }
                        }
                        await ws.send_str(json.dumps(response))
                        await ws.send_bytes(validation_token.encode('utf-8'))

                    return

            # Parse body
            if isinstance(body, str):
                # Body is a string - parse it
                if body.startswith("validationToken="):
                    # Validation token format
                    token = body.split("=", 1)[1] if "=" in body else body
                    notification = {"validationToken": token}
                    logger.info(f"Detected validation token format: {body[:50]}...")
                else:
                    # Try parsing as JSON
                    try:
                        notification = json.loads(body)
                    except json.JSONDecodeError:
                        notification = {"raw": body}
            elif body is True:
                # Body is True - actual content in next binary frame
                # For now, wait for next message (this is typically for larger payloads)
                logger.info("Body content will be in next frame, waiting for binary data...")
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=5.0)
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        body_data = msg.data.decode('utf-8')
                        logger.info(f"Received body frame: {body_data[:200]}")
                        try:
                            notification = json.loads(body_data)
                        except json.JSONDecodeError:
                            notification = {"raw": body_data}
                    else:
                        logger.warning(f"Expected binary frame, got {msg.type}")
                        notification = {}
                except asyncio.TimeoutError:
                    logger.warning("Timeout waiting for body frame")
                    notification = {}
            else:
                # Body is False or None
                notification = {}

            logger.info(f"Parsed notification: {json.dumps(notification, indent=2) if notification else 'empty'}")

            # Handle Microsoft Graph validation token
            if "validationToken" in notification:
                logger.info("Responding to Microsoft Graph validation request")
                response_body = json.dumps({"validationToken": notification["validationToken"]})
                await self._send_response(ws, request_id, 200, response_body)
                return

            # Call user callback
            if self.callback:
                try:
                    result = await self.callback(notification)
                    response_body = json.dumps(result if result else {"status": "processed"})
                    await self._send_response(ws, request_id, 200, response_body)
                except Exception as e:
                    logger.error(f"Callback error: {e}")
                    error_body = json.dumps({"error": str(e)})
                    await self._send_response(ws, request_id, 500, error_body)
            else:
                await self._send_response(ws, request_id, 200, json.dumps({"status": "ok"}))

        except Exception as e:
            logger.error(f"Error handling message: {e}")

    async def _send_rendezvous_response(self, rendezvous_address: str, request_id: str, validation_token: str):
        """
        Establish rendezvous WebSocket connection and send response.

        When sb-hc-action=request is in the address, Azure Relay requires
        responses to be sent over a separate WebSocket connection to that address.

        OPTIMIZED: Uses shared session for faster connection, no artificial delays.
        """
        import time
        start = time.time()

        try:
            logger.debug(f"Connecting to rendezvous WebSocket: {rendezvous_address[:80]}...")

            # Use shared session for faster connection (reuses connection pool)
            async with self._session.ws_connect(rendezvous_address) as rendezvous_ws:
                connect_time = time.time() - start
                logger.info(f"Rendezvous WebSocket connected in {connect_time:.3f}s")

                # Send response - Microsoft Graph expects status 200 and plain text body
                response = {
                    "response": {
                        "requestId": request_id,
                        "statusCode": "200",
                        "statusDescription": "OK",
                        "responseHeaders": {
                            "Content-Type": "text/plain"
                        },
                        "body": True  # CRITICAL: Tells Azure Relay to expect binary body frames
                    }
                }
                await rendezvous_ws.send_str(json.dumps(response))

                # Send validation token as binary body frame immediately
                body_data = validation_token.encode('utf-8')
                await rendezvous_ws.send_bytes(body_data)

                total_time = time.time() - start
                logger.info(f"Validation response sent in {total_time:.3f}s (token: {len(body_data)} bytes)")
                # Connection automatically closed by context manager - no sleep needed

        except Exception as e:
            logger.error(f"Error in rendezvous response: {e}", exc_info=True)

    async def _send_response(self, ws, request_id: str, status_code: int, body: str):
        """
        Send HTTP response back through WebSocket using Azure Relay protocol.

        Azure Relay protocol requires:
        - requestId must match the request's id field
        - statusCode must be a string, not int
        - responseHeaders, not headers
        - "body": true/false to indicate if binary frames follow
        - Body content sent as binary frame after JSON response
        """
        response = {
            "response": {
                "requestId": request_id,
                "statusCode": str(status_code),  # Must be string per Azure Relay protocol
                "statusDescription": "OK" if status_code == 200 else "Error",
                "responseHeaders": {
                    "Content-Type": "application/json"
                },
                "body": bool(body)  # CRITICAL: Set based on whether body exists
            }
        }

        logger.info(f"Sending response: status={status_code}, requestId={request_id}, has_body={bool(body)}")

        # Send response JSON frame
        await ws.send_str(json.dumps(response))

        # Send body as binary frame (only if body exists)
        if body:
            await ws.send_bytes(body.encode('utf-8'))

    async def stop(self):
        """Stop listening."""
        logger.info("Stopping Azure Relay listener")
        self.running = False
