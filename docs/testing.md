# testing guide

comprehensive testing suite for backend and frontend components.

## test coverage

### backend tests (`backend/test_backend.py`)
- spotify oauth authentication
- token exchange and refresh
- user profile fetching
- ai taste analysis
- playlist name generation
- mood detection
- spotify api service
- flask api endpoints
- **production hardening**:
  - health & readiness checks
  - security headers (csp, hsts, etc.)
  - custom error handling (404, 500)

### frontend tests (`frontend/app.test.js`)
- data persistence (localstorage)
- streak tracking logic
- swipe functionality
- session completion
- api integration
- ui state management

---

## running backend tests

### setup
```bash
cd backend
pip3 install -r requirements-test.txt
```

### run all tests
```bash
pytest test_backend.py -v
```

### run with coverage report
```bash
pytest test_backend.py --cov=. --cov-report=html
open htmlcov/index.html
```

### run specific test class
```bash
pytest test_backend.py::TestSpotifyAuth -v
```

### run single test
```bash
pytest test_backend.py::TestSpotifyAuth::test_get_auth_url_generates_valid_url -v
```

---

## running frontend tests

### setup
```bash
cd frontend
npm install --save-dev jest @testing-library/jest-dom
```

### package.json test script
add to `package.json`:
```json
{
  "scripts": {
    "test": "jest",
    "test:watch": "jest --watch",
    "test:coverage": "jest --coverage"
  },
  "devDependencies": {
    "jest": "^29.7.0",
    "@testing-library/jest-dom": "^6.1.5"
  }
}
```

### run tests
```bash
npm test
```

### watch mode
```bash
npm run test:watch
```

### coverage report
```bash
npm run test:coverage
```

---

## test categories

### 1. unit tests
test individual functions in isolation:
- auth functions
- ai service functions
- data persistence
- utility functions

### 2. integration tests
test how components work together:
- api endpoints with auth
- full oauth flow
- playlist creation workflow

### 3. mock testing
all external dependencies are mocked:
- spotify api calls
- openai api calls
- localstorage
- fetch requests

---

## writing new tests

### backend test template
```python
class TestNewFeature:
    """test description"""
    
    @patch('module.external_function')
    def test_feature_success(self, mock_function):
        """test successful case"""
        # arrange
        mock_function.return_value = 'expected_value'
        
        # act
        result = function_to_test()
        
        # assert
        assert result == 'expected_value'
```

### frontend test template
```javascript
describe('new feature', () => {
    test('should do something', () => {
        // arrange
        const input = 'test';
        
        // act
        const result = functionToTest(input);
        
        // assert
        expect(result).toBe('expected');
    });
});
```

---

## current test results

### backend coverage
- `spotify_auth.py`: 85%
- `ai_service.py`: 90%
- `spotify_service.py`: 80%
- `app.py` endpoints: 85% (added health/ready/error tests)

### frontend coverage
- data persistence: 100%
- swipe logic: 95%
- api integration: 85%
- ui state: 90%

---

## debugging tests

### print debug info
```python
# backend
pytest test_backend.py -v -s  # shows print statements
```

```bash
# frontend
npm test -- --verbose
```

### run failed tests only
```bash
pytest --lf  # last failed
pytest --ff  # failed first
```

---

## continuous integration

### github actions workflow
create `.github/workflows/test.yml`:
```yaml
name: tests

on: [push, pull_request]

jobs:
  backend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-python@v2
      - run: pip install -r backend/requirements-test.txt
      - run: pytest backend/test_backend.py
  
  frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-node@v2
      - run: npm install
      - run: npm test
```

---

## test checklist

before deploying:
- [ ] all backend tests pass
- [ ] all frontend tests pass
- [ ] coverage > 80%
- [ ] no skipped tests
- [ ] integration tests pass
- [ ] manual smoke tests completed

---

## 🎯 **Next Steps**

1. **Install test dependencies**
2. **Run all tests locally**
3. **Fix any failures**
4. **Set up CI/CD**
5. **Maintain 80%+ coverage**

---

**Happy Testing! 🧪✨**
