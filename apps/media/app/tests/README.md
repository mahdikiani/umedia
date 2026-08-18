# Media Application Tests

This directory contains comprehensive tests for the media application, covering file management, models, routes, and integration workflows.

## Test Structure

### Test Files

- **`test_file_manager.py`** - Tests for the FileManager class
  - File processing and upload workflows
  - File change and update operations
  - Storage backend interactions
  - Error handling and edge cases

- **`test_models.py`** - Tests for FileMetaData and ObjectMetaData models
  - CRUD operations
  - Permission management
  - Directory operations
  - Deletion workflows (soft/hard delete)
  - Volume calculations

- **`test_routes.py`** - Tests for FilesRouter and download endpoints
  - File upload endpoints
  - File download and streaming
  - Authentication and authorization
  - Error handling in routes

- **`test_integration.py`** - Integration tests for complete workflows
  - End-to-end file upload to download
  - Directory management workflows
  - Permission workflows
  - Error handling scenarios

- **`test_config.py`** - Test configuration and utilities
  - Test environment setup
  - Mock storage backend
  - Test data factories
  - Database cleanup utilities

- **`run_tests.py`** - Test runner script
  - Comprehensive test execution
  - Coverage reporting
  - Pattern-based test selection

## Running Tests

### Prerequisites

1. Install test dependencies:
```bash
pip install pytest pytest-asyncio pytest-cov mongomock-motor
```

2. Ensure the application is properly configured for testing.

### Basic Test Execution

Run all tests:
```bash
python tests/run_tests.py
```

Run with coverage:
```bash
python tests/run_tests.py --coverage
```

### Specific Test Categories

Run only FileManager tests:
```bash
python tests/run_tests.py --file-manager
```

Run only model tests:
```bash
python tests/run_tests.py --models
```

Run only route tests:
```bash
python tests/run_tests.py --routes
```

Run only integration tests:
```bash
python tests/run_tests.py --integration
```

### Pattern-Based Testing

Run tests matching a pattern:
```bash
python tests/run_tests.py --pattern "upload"
python tests/run_tests.py --pattern "permission"
```

### Using pytest directly

Run specific test file:
```bash
pytest tests/test_file_manager.py -v
```

Run with coverage:
```bash
pytest tests/ --cov=apps --cov-report=html
```

## Test Coverage

The tests cover the following areas:

### FileManager Tests
- ✅ File processing and upload
- ✅ File change and update operations
- ✅ Storage backend interactions
- ✅ Error handling (upload failures, invalid inputs)
- ✅ Presigned URL generation
- ✅ File streaming and download
- ✅ Duplicate file handling

### Model Tests
- ✅ FileMetaData CRUD operations
- ✅ ObjectMetaData operations
- ✅ Directory creation and management
- ✅ Permission system (owner, user, public)
- ✅ Soft and hard deletion workflows
- ✅ File restoration
- ✅ Volume calculations
- ✅ Orphaned file cleanup

### Route Tests
- ✅ File upload endpoints (multipart, base64, URL)
- ✅ File download and streaming
- ✅ Authentication and authorization
- ✅ Range request handling
- ✅ Error responses
- ✅ Directory listing

### Integration Tests
- ✅ Complete file upload to download workflow
- ✅ File update workflows
- ✅ Directory operations
- ✅ Permission workflows
- ✅ Deletion workflows
- ✅ Error handling scenarios
- ✅ Duplicate file handling
- ✅ Base64 upload workflows

## Test Configuration

### Environment Variables

The tests use the following environment variables:
- `STORAGE_BACKEND=local` - Use local storage for testing
- `PUBLIC_ACCESS_TYPE=READ` - Set default public access
- `PROJECT_NAME=test_project` - Test project name
- `ROOT_URL=test.example.com` - Test root URL
- `BASE_PATH=/api/media/v1/` - Test base path

### Database Setup

Tests use `mongomock-motor` for in-memory MongoDB testing:
- No external database required
- Automatic cleanup between tests
- Isolated test environments

### Mock Storage Backend

Tests include a mock storage backend that:
- Simulates file upload/download operations
- Provides predictable responses
- Allows testing without external storage services

## Test Data

### Test Data Factory

The `TestDataFactory` class provides utilities for creating test data:
- File metadata creation
- Object metadata creation
- Consistent test data across tests

### Fixtures

Common fixtures available:
- `file_manager` - FileManager instance
- `files_router` - FilesRouter instance
- `sample_file` - Sample file metadata
- `upload_file` - Sample upload file
- `mock_request` - Mock HTTP request
- `mock_user` - Mock user data

## Coverage Reports

Tests generate multiple coverage reports:
- **Terminal output** - Shows missing lines
- **HTML report** - Detailed coverage in `htmlcov/`
- **XML report** - For CI/CD integration

### Coverage Targets

- **FileManager**: 95%+ coverage
- **Models**: 90%+ coverage
- **Routes**: 85%+ coverage
- **Overall**: 80%+ coverage

## Best Practices

### Writing New Tests

1. **Use descriptive test names** that explain what is being tested
2. **Follow the Arrange-Act-Assert pattern**
3. **Test both success and failure scenarios**
4. **Use appropriate fixtures for common setup**
5. **Mock external dependencies**
6. **Test edge cases and error conditions**

### Test Organization

1. **Unit tests** - Test individual components in isolation
2. **Integration tests** - Test component interactions
3. **End-to-end tests** - Test complete workflows

### Error Testing

1. **Test expected exceptions** with proper assertions
2. **Verify error messages and status codes**
3. **Test edge cases** that might cause errors
4. **Test permission denials** and authorization failures

## Troubleshooting

### Common Issues

1. **Import errors**: Ensure the app directory is in Python path
2. **Database connection errors**: Check mongomock-motor installation
3. **Storage backend errors**: Verify mock storage configuration
4. **Permission errors**: Check test user setup

### Debug Mode

Enable debug mode for detailed output:
```bash
DEBUGPY=true python tests/run_tests.py
```

### Verbose Output

Get detailed test output:
```bash
python tests/run_tests.py -v
```

## Continuous Integration

The tests are designed to work in CI/CD environments:
- Use in-memory database (no external dependencies)
- Generate coverage reports for quality gates
- Provide clear pass/fail indicators
- Support parallel test execution

## Performance

Test execution time targets:
- **Unit tests**: < 1 second per test
- **Integration tests**: < 5 seconds per test
- **Full test suite**: < 30 seconds total

## Contributing

When adding new features:
1. Write tests first (TDD approach)
2. Ensure all tests pass
3. Maintain or improve coverage
4. Update this README if needed 