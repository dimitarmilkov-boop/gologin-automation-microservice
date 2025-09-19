# GoLogin Automation Service

A FastAPI microservice that automates Twitter/X account reauthorization through GoLogin browser profiles. This service acts as a browser automation layer between AIOTT V2 and GoLogin profiles.

## Architecture

```
AIOTT V2 (Railway)
    ↓ [API Request]
GoLogin Microservice (Hetzner: 65.108.246.91)
    ↓ [Controls]
GoLogin Profile (Browser with proxy)
    ↓ [Navigates]
AIOTT Web App → Twitter/X OAuth → Callback to AIOTT
```

## Features

- **Automated OAuth Flow**: Handles complete Twitter/X OAuth authorization
- **Profile Management**: Manages up to 10 concurrent GoLogin profiles
- **Token Management**: Stores and refreshes OAuth tokens
- **Error Handling**: Comprehensive error reporting with X-specific error codes
- **API Security**: Token-based authentication
- **Monitoring**: Health checks and metrics

## API Endpoints

### Authorization

**POST** `/api/v1/authorize`

```json
{
  "account_id": "twitter_username",
  "action": "authorize",
  "api_app": "AIOTT1|AIOTT2|AIOTT3"
}
```

**Response (Success):**
```json
{
  "status": "success",
  "oauth_token": "...",
  "oauth_token_secret": "...",
  "refresh_token": "...",
  "scopes": ["read", "write", "dm"]
}
```

**Response (Error):**
```json
{
  "status": "error",
  "error_code": "X_ERROR_CODE",
  "message": "Authorization failed: user denied"
}
```

### Profile Management

**GET** `/api/v1/profiles` - List all profiles
**GET** `/api/v1/profiles/{account_id}` - Get specific profile
**POST** `/api/v1/profiles/sync` - Sync profiles from GoLogin

### Monitoring

**GET** `/health` - Health check
**GET** `/` - Service info

## Setup

### Environment Configuration

Copy `.env.example` to `.env` and configure:

```bash
# Database
DATABASE_URL=postgresql://user:password@localhost:5432/gologin_automation

# GoLogin
GOLOGIN_TOKEN=your_gologin_api_token

# Security
API_SECRET_KEY=your-secret-key

# Twitter OAuth Apps
AIOTT1_CLIENT_ID=your_client_id
AIOTT1_CLIENT_SECRET=your_client_secret
AIOTT1_CALLBACK_URL=https://thefeedwire.com/callback/aiott1
```

### Database Setup

```bash
python scripts/setup_database.py
```

### Local Development

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Production Deployment

```bash
docker-compose up -d
```

## Usage

### Authentication

All API requests require the `X-API-Key` header:

```bash
curl -H "X-API-Key: your-api-key" https://thefeedwire.com/api/v1/profiles
```

### Authorization Flow

1. **Request Authorization**:
```bash
curl -X POST https://thefeedwire.com/api/v1/authorize \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "account_id": "testuser",
    "action": "authorize",
    "api_app": "AIOTT1"
  }'
```

2. **Service Flow**:
   - Finds GoLogin profile for account
   - Starts profile with proxy
   - Navigates to Twitter OAuth
   - Automates authorization clicks
   - Captures OAuth tokens
   - Returns tokens to client

## Configuration

### Profile Limits
- Max concurrent profiles: **10** (hardcoded)
- Profile sync interval: **15-30 minutes**
- Browser timeout: **30 seconds**

### Security
- API key authentication
- HTTPS only (production)
- Token encryption in database
- Request rate limiting

## Monitoring

### Health Checks
```bash
curl https://thefeedwire.com/health
```

### Logs
- Application logs: `/app/logs/`
- Access logs: Nginx
- Error tracking: Sentry (optional)

## Error Codes

| Code | Description |
|------|-------------|
| `BROWSER_AUTOMATION_FAILED` | Browser automation error |
| `TOKEN_EXCHANGE_FAILED` | OAuth token exchange failed |
| `PROFILE_NOT_FOUND` | No GoLogin profile for account |
| `AUTHORIZATION_TIMEOUT` | Process timed out |
| `USER_DENIED` | User denied authorization |

## Testing

```bash
python scripts/test_auth.py
```

## Deployment

### Server Requirements
- Ubuntu 20.04+
- Docker & Docker Compose
- 4GB RAM minimum
- GoLogin subscription

### SSL Setup
```bash
# Install certbot
sudo apt install certbot

# Generate certificate
sudo certbot certonly --standalone -d thefeedwire.com

# Copy certificates
sudo cp /etc/letsencrypt/live/thefeedwire.com/fullchain.pem ./ssl/
sudo cp /etc/letsencrypt/live/thefeedwire.com/privkey.pem ./ssl/
```

### Production Start
```bash
docker-compose -f docker-compose.yml up -d
```

## Support

For issues and feature requests, contact the development team.

## License

Proprietary - TheSoul Publishing