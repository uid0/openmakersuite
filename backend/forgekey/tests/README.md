# ForgeKey Test Suite

This directory contains comprehensive tests for the ForgeKey application.

## Test Structure

### `factories.py`
Factory classes for creating test data using factory_boy:
- `UserFactory` - Creates test users
- `DeviceTypeFactory` - Creates device types
- `ESP32DeviceFactory` - Creates ESP32 devices
- `AssetDeviceFactory` - Creates asset-device relationships
- `OperationalModeFactory` - Creates operational modes
- `AssetAuthorizationFactory` - Creates authorizations
- `DeviceLockoutFactory` - Creates lockouts
- `DeviceUsageFactory` - Creates usage sessions
- `PowerMeterReadingFactory` - Creates power readings
- `FirmwareVersionFactory` - Creates firmware versions
- `DeviceFirmwareUpdateFactory` - Creates firmware updates

### `test_models.py`
Unit tests for all ForgeKey models:
- DeviceType model tests
- ESP32Device model tests (MAC normalization, JWT secret generation)
- AssetDevice relationship tests
- OperationalMode tests
- AssetAuthorization tests
- DeviceLockout tests (including permission hierarchy)
- DeviceUsage tests (session tracking)
- PowerMeterReading tests
- FirmwareVersion tests
- DeviceFirmwareUpdate tests

### `test_api.py`
API endpoint tests:
- ESP32Device API (list, detail, enable, disable, status)
- OperationalMode API (list, enable/disable classroom mode)
- AssetAuthorization API (list, add via classroom mode)
- DeviceLockout API (list, create, unlock)

### `test_utils.py`
Utility function tests:
- MAC address normalization
- MQTT topic generation
- JWT token generation and verification

### `test_tasks.py`
Celery task tests (with mocked MQTT):
- MQTT command sending
- Device enable/disable tasks
- Status request tasks
- MQTT message processing (status, power readings)

### `test_lockout_permissions.py`
Comprehensive hierarchical lockout permission tests:
- User can unlock own lockout
- COO can unlock any lockout
- Logistics Lead can unlock lower levels
- Logistics Team can unlock lower levels
- Maintainer can unlock user level
- Users cannot unlock other users' lockouts
- Superuser can unlock any lockout

## Running Tests

Run all ForgeKey tests:
```bash
pytest forgekey/tests/
```

Run specific test file:
```bash
pytest forgekey/tests/test_models.py
```

Run with coverage:
```bash
pytest forgekey/tests/ --cov=forgekey --cov-report=html
```

## Known Issues

Some tests that use Django Groups may fail if the test database migrations haven't fully created the `auth_user_groups` table. This is typically resolved by ensuring all migrations are applied. The tests use `groups.set()` instead of `groups.add()` to work around this in some cases.

## Test Coverage Goals

- Models: 100% coverage of model methods and properties
- Views/API: Test all CRUD operations and custom actions
- Utils: Test all utility functions
- Tasks: Test all Celery tasks with mocked MQTT
- Permissions: Test all hierarchical permission scenarios
