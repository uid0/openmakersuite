# EMQX JWT Authentication Setup Guide

## Overview
This guide walks you through setting up EMQX to accept JWT tokens as MQTT passwords for authentication.

## Step 1: Access EMQX Dashboard

1. **Open your browser** and navigate to: `http://192.168.1.48:18083`
2. **Login** with default credentials:
   - Username: `admin`
   - Password: `public`

## Step 2: Configure JWT Authentication (Web UI)

### Method A: Using the Web Dashboard

1. **Navigate to Authentication**:
   - Go to **Authentication** → **Authentication** in the left sidebar
   - Click **Create** button

2. **Select JWT Authentication**:
   - Choose **JWT** as the authentication method
   - Click **Next**

3. **Configure JWT Settings**:
   ```
   Authentication Name: jwt_auth
   JWT Secret: JWTSecret
   JWT From: password
   Verify Claims: true
   ```

4. **Add Required Claims** (click **Add Claim** for each):
   ```
   Claim Name: iss
   Expected Value: esp32-sensor
   
   Claim Name: aud
   Expected Value: emqx
   
   Claim Name: sub
   Expected Value: (leave empty - will be validated against username)
   ```

5. **Save Configuration**

## Step 3: Configure JWT Authentication (HTTP API)

### Method B: Using HTTP API

```bash
curl -X POST "http://192.168.1.48:18083/api/v5/authentication" \
-H "Content-Type: application/json" \
-u admin:public \
-d '{
  "mechanism": "jwt",
  "backend": "built_in_database",
  "jwt_secret": "JWTSecret",
  "jwt_from": "password",
  "verify_claims": true,
  "claims": {
    "iss": "esp32-sensor",
    "aud": "emqx"
  }
}'
```

## Step 4: Verify Configuration

1. **Check Authentication Status**:
   - Go to **Authentication** → **Authentication**
   - Verify your JWT authentication is listed and enabled

2. **Test with MQTT Client**:
   ```bash
   # Install mosquitto client
   sudo apt-get install mosquitto-clients
   
   # Test connection with JWT token
   mosquitto_pub -h 192.168.1.48 -p 1883 \
     -u "8CBFEA8EA40C" \
     -P "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..." \
     -t "test/topic" \
     -m "Hello EMQX"
   ```

## Step 5: JWT Token Structure

Your ESP32-S3 will generate JWT tokens with this structure:

```json
{
  "iss": "esp32-sensor",
  "sub": "8CBFEA8EA40C",
  "aud": "emqx",
  "exp": 1234567890,
  "iat": 1234564290,
  "device_id": "esp32s3_sensor_01",
  "mac_address": "8C:BF:EA:8E:A4:0C"
}
```

## Step 6: Troubleshooting

### Common Issues:

1. **JWT Secret Mismatch**:
   - Ensure `JWT_SECRET` in ESP32 code matches EMQX configuration
   - Current secret: `JWTSecret`

2. **Claims Validation**:
   - Verify `iss` claim is set to `esp32-sensor`
   - Verify `aud` claim is set to `emqx`
   - Verify `sub` claim matches the MQTT username (MAC address)

3. **Token Expiration**:
   - JWT tokens expire after 1 hour
   - ESP32 will regenerate tokens automatically

4. **Network Connectivity**:
   - Ensure ESP32 can reach EMQX server at `192.168.1.48:1883`
   - Check firewall settings

### Debug Commands:

```bash
# Check EMQX status
curl -u admin:public http://192.168.1.48:18083/api/v5/status

# List authentication methods
curl -u admin:public http://192.168.1.48:18083/api/v5/authentication

# Check MQTT connectivity
telnet 192.168.1.48 1883
```

## Step 7: Test the Complete Setup

1. **Upload the updated ESP32 code**
2. **Monitor serial output** for JWT generation and MQTT connection
3. **Verify MQTT connection** in EMQX dashboard under **Monitoring** → **Connections**

## Expected Behavior

- ESP32 connects to WiFi
- Generates JWT token with proper claims
- Connects to EMQX using MAC address as username and JWT as password
- Publishes temperature data to `sensor_a40c/sensors/temperature`
- Subscribes to `sensor_a40c/light/control` for commands

## Security Notes

- JWT secret should be changed in production
- Consider using HTTPS for EMQX dashboard
- Implement proper network security
- Monitor authentication logs in EMQX
