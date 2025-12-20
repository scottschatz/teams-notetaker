# Contributing to Teams Meeting Transcript Summarizer

Thank you for your interest in contributing to the Teams Meeting Transcript Summarizer! This document provides guidelines for contributing to the project.

## Getting Started

### Prerequisites
- Python 3.11+
- PostgreSQL 12+
- Git
- Familiarity with FastAPI, SQLAlchemy, and async Python

### Development Setup

1. **Fork and Clone**
   ```bash
   git clone https://github.com/yourusername/teams-notetaker.git
   cd teams-notetaker
   ```

2. **Create Virtual Environment**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Configure Environment**
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

4. **Initialize Database**
   ```bash
   python -m src.main db init
   ```

5. **Run Tests**
   ```bash
   pytest tests/ -v
   ```

## Development Workflow

### 1. Create a Feature Branch

```bash
git checkout -b feature/your-feature-name
# or
git checkout -b fix/your-bug-fix
```

### 2. Make Your Changes

Follow the code standards outlined below. Key areas:
- **Backend**: `src/` directory structure
- **Web Dashboard**: `src/web/` for FastAPI routes and templates
- **Job Processors**: `src/jobs/processors/` for async job processing
- **Database Models**: `src/core/database.py`

### 3. Write Tests

Add tests for new functionality:
```bash
# Create test file in tests/ directory
tests/test_your_feature.py

# Run tests
pytest tests/test_your_feature.py -v
```

### 4. Update Documentation

Update relevant documentation:
- **README.md** - If adding user-facing features
- **CLAUDE.md** - If changing architecture or patterns
- **ARCHITECTURE.md** - If modifying system design
- **DEPLOYMENT.md** - If changing deployment procedures
- Docstrings in code

### 5. Commit Your Changes

```bash
git add .
git commit -m "Add feature: description of your changes"
```

**Commit Message Guidelines**:
- Use present tense ("Add feature" not "Added feature")
- Be descriptive but concise
- Reference issues if applicable (#123)

### 6. Push and Create Pull Request

```bash
git push origin feature/your-feature-name
```

Then create a pull request on GitHub with:
- Clear title and description
- Reference to any related issues
- Description of changes made
- Testing performed
- Screenshots (if UI changes)

## Code Standards

### Python Code Style

- **Type Hints**: Use type hints throughout
  ```python
  def process_meeting(meeting_id: int) -> Dict[str, Any]:
      ...
  ```

- **Docstrings**: Use Google-style docstrings
  ```python
  def fetch_transcript(meeting_id: int) -> str:
      """Fetch VTT transcript for a meeting.

      Args:
          meeting_id: The ID of the meeting

      Returns:
          VTT transcript content as string

      Raises:
          TranscriptNotFoundError: If transcript doesn't exist
      """
  ```

- **Error Handling**: Use specific exceptions and handle errors appropriately
  ```python
  try:
      result = api_call()
  except SpecificError as e:
      logger.error(f"Failed to process: {e}")
      raise ProcessingError(f"Failed: {e}") from e
  ```

- **Logging**: Use appropriate log levels
  ```python
  logger.debug("Detailed debug information")
  logger.info("General information")
  logger.warning("Warning message")
  logger.error("Error occurred")
  ```

### Async Code

When writing async processors:
- All blocking I/O must use `run_in_executor`
- Use `async def` for processor methods
- Properly handle async context managers

```python
async def process(self, job: JobQueue) -> Dict[str, Any]:
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: blocking_call(...)
    )
    return result
```

### Database

- Use SQLAlchemy ORM (no raw SQL except for optimizations)
- Always use context managers for sessions
- Write migrations for schema changes

```python
with self.db.get_session() as session:
    meeting = session.query(Meeting).filter_by(id=meeting_id).first()
    # ... work with meeting
# Session automatically closes
```

### Web Routes

- Use FastAPI dependency injection
- Return Pydantic models or dict responses
- Handle errors with HTTPException

```python
@router.get("/meetings/{meeting_id}")
async def get_meeting(
    meeting_id: int,
    db: DatabaseManager = Depends(get_db)
) -> Dict[str, Any]:
    meeting = db.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting.to_dict()
```

## Testing Guidelines

### Test Structure

```python
import pytest
from src.core.database import Meeting

def test_meeting_creation():
    """Test that meetings are created correctly."""
    meeting = Meeting(
        meeting_id="test-123",
        subject="Test Meeting",
        organizer_email="test@example.com"
    )
    assert meeting.subject == "Test Meeting"
```

### Mocking External APIs

```python
from unittest.mock import Mock, patch

@patch('src.graph.client.GraphAPIClient.get')
def test_transcript_fetch(mock_get):
    """Test transcript fetching with mocked Graph API."""
    mock_get.return_value = {"value": [{"id": "transcript-1"}]}
    # ... test code
```

### Running Tests

```bash
# Run all tests
pytest tests/

# Run specific test file
pytest tests/test_transcript.py -v

# Run with coverage
pytest --cov=src tests/

# Run specific test
pytest tests/test_transcript.py::test_fetch_transcript -v
```

## Adding New Features

### New Job Processor

1. Create processor in `src/jobs/processors/`
   ```python
   from .base import BaseJobProcessor

   class YourProcessor(BaseJobProcessor):
       async def process(self, job: JobQueue) -> Dict[str, Any]:
           # Implementation
           pass
   ```

2. Register in `src/jobs/processors/__init__.py`
3. Add job type to `JobType` enum in `src/core/database.py`
4. Write tests in `tests/test_your_processor.py`

### New Web Route

1. Create router in `src/web/routers/your_router.py`
2. Create template in `src/web/templates/your_template.html`
3. Register router in `src/web/app.py`:
   ```python
   from .routers import your_router
   app.include_router(your_router.router)
   ```

### Database Changes

1. Update models in `src/core/database.py`
2. Create migration SQL in `migrations/YYYYMMDD_description.sql`
3. Document in migration file's header
4. Test migration on clean database

## Documentation

All contributions should include documentation updates:

- **Code Comments**: Explain complex logic
- **Docstrings**: All public functions and classes
- **README.md**: User-facing feature changes
- **CLAUDE.md**: Architecture/pattern changes for AI assistants
- **ARCHITECTURE.md**: System design changes
- **DEPLOYMENT.md**: Deployment procedure changes

## Pull Request Process

1. **Self-Review**: Review your own changes first
2. **Tests Pass**: Ensure all tests pass
3. **Documentation**: Update all relevant docs
4. **Description**: Write clear PR description
5. **Respond to Feedback**: Address review comments promptly

### PR Checklist

- [ ] Code follows style guidelines
- [ ] Tests added for new functionality
- [ ] All tests pass
- [ ] Documentation updated
- [ ] No merge conflicts
- [ ] Commit messages are clear
- [ ] Changes are focused (one feature/fix per PR)

## Code Review Process

### For Reviewers

- Be constructive and respectful
- Focus on code quality, not personal preferences
- Test the changes locally if possible
- Approve when ready or request changes with specific feedback

### For Contributors

- Respond to all review comments
- Make requested changes or discuss alternatives
- Re-request review after making changes
- Be patient - reviews may take time

## Common Contribution Areas

### Easy Contributions

- Documentation improvements
- Bug fixes
- Test coverage improvements
- UI/UX enhancements
- Performance optimizations

### Medium Complexity

- New job processors
- Additional web dashboard pages
- Email template improvements
- New CLI commands
- Database query optimizations

### Advanced Contributions

- Core architecture changes
- New discovery mechanisms
- Advanced AI prompt engineering
- Multi-tenant support
- Performance profiling and optimization

## Getting Help

### Resources

- **Documentation**: Read [README.md](README.md), [ARCHITECTURE.md](ARCHITECTURE.md), [CLAUDE.md](CLAUDE.md)
- **Issues**: Check existing [GitHub Issues](https://github.com/scottschatz/teams-notetaker/issues)
- **Code**: Review existing code for patterns and examples

### Questions

If you have questions:
1. Check existing documentation first
2. Search closed issues for similar questions
3. Open a new issue with the "question" label
4. Be specific about what you're trying to accomplish

## License

By contributing to this project, you agree that your contributions will be licensed under the same license as the project.

## Recognition

Contributors will be recognized in:
- Git commit history
- Release notes (for significant contributions)
- README acknowledgments section (for major features)

---

## Quick Reference

### Useful Commands

```bash
# Development
source venv/bin/activate
python -m src.main serve --port 8000 --reload

# Testing
pytest tests/ -v
pytest --cov=src tests/

# Code Quality
black src/  # Format code (if configured)
mypy src/   # Type checking (if configured)

# Database
python -m src.main db init
python -m src.main db health
psql -U postgres -d teams_notetaker

# Services (if deployed)
systemctl --user restart teams-notetaker-poller
journalctl --user -u teams-notetaker-poller -f
```

### File Structure

```
teams-notetaker/
├── src/
│   ├── ai/              # Claude API integration
│   ├── core/            # Database models, config
│   ├── graph/           # Microsoft Graph API
│   ├── jobs/            # Job queue and processors
│   ├── web/             # FastAPI dashboard
│   └── webhooks/        # Webhook handling
├── tests/               # Test files
├── migrations/          # Database migrations
├── docs/                # Additional documentation
└── scripts/             # Utility scripts
```

---

Thank you for contributing! Your efforts help make this project better for everyone.

**Questions?** Open an issue or reach out to the maintainers.

---

**Last Updated**: 2025-12-19
