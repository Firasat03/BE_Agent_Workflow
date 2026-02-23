# My Project Coding Rules
# ─────────────────────────────────────────────────────────────────────────────
# This file is loaded at startup and injected into EVERY agent's system prompt.
# Edit this file to enforce your team's coding standards across all generated code.
# ─────────────────────────────────────────────────────────────────────────────

## Naming Conventions
- Use snake_case for functions, variables, and file names
- Use PascalCase for class names
- Prefix interfaces/abstract classes with `I` (e.g. IUserRepository)
- Suffix DTOs with `DTO` (e.g. UserRequestDTO, UserResponseDTO)
- Suffix repositories with `Repository` (e.g. UserRepository)
- Suffix services with `Service` (e.g. UserService)
- Test files must be named `test_<module_name>.py` and placed in `tests/`

## Code Patterns (Architectural Rules)
- Always use the Repository Pattern for all database access
- Services must be stateless — no instance-level mutable state
- No business logic in controllers/handlers — delegate to service layer
- Use DTOs for all request/response objects — NEVER expose DB models directly
- All endpoints must return a standard JSON envelope:
  { "data": <payload>, "error": null, "status_code": 200 }
- Prefer composition over inheritance

## Security Rules
- NEVER log sensitive fields: password, token, secret, api_key, ssn, credit_card
- Always validate and sanitize ALL user inputs
- Use parameterized queries — NEVER use string interpolation in SQL
- JWT tokens must have an expiry of ≤ 24 hours
- Hash passwords using bcrypt with a minimum cost factor of 12
- Sensitive config (DB credentials, API keys) must come from environment variables — never hardcoded

## Error Handling
- Always use custom exception classes (e.g. ResourceNotFoundException, ValidationException)
- Use a single GlobalExceptionHandler / error middleware to handle all exceptions
- NEVER swallow exceptions silently (empty except/catch blocks are forbidden)
- Always include a user-friendly error message in error responses
- Log exceptions at ERROR level with full stack trace

## Testing Rules
- Every new public function/method must have at least one unit test
- Mock ALL external dependencies (database, HTTP calls, file I/O) in unit tests
- Integration tests must NOT connect to a real database — use SQLite in-memory or Testcontainers
- Test coverage must be ≥ 80% for new code
- Use descriptive test names: `test_<what>_when_<condition>_should_<expected>`

## Response Format
- All API responses must follow the standard envelope: {data, error, status_code}
- Error responses must include: {data: null, error: {code, message}, status_code}
- Use HTTP status codes correctly (200 OK, 201 Created, 400 Bad Request, 404 Not Found, 500 Internal Server Error)

## Documentation
- Every public class and method must have a docstring
- Include type hints for all function parameters and return values
- README must be updated with any new endpoint or feature added
