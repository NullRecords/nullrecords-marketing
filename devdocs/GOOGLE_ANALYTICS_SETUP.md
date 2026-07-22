# Google Analytics Setup Instructions

## Current Status: Mock Data

The NullRecords Marketing currently uses **mock/fake Google Analytics data** for testing purposes. This document explains how to enable real Google Analytics integration.

## Evidence of Mock Data

### Code Location: `scripts/daily_report.py`

```python
def _generate_mock_ga_data(self):
    """Generate mock Google Analytics data for testing"""
    self.metrics.website_visitors = random.randint(150, 350)
    self.metrics.website_pageviews = random.randint(400, 800) 
    self.metrics.website_sessions = random.randint(180, 400)
    self.metrics.bounce_rate = random.uniform(35.0, 65.0)
    self.metrics.avg_session_duration = random.uniform(120.0, 300.0)
    self.metrics.top_pages = [
        ("Homepage", random.randint(50, 150)),
        ("About", random.randint(20, 80)),
        ("Music", random.randint(30, 100)),
        ("Contact", random.randint(10, 50)),
        ("Blog", random.randint(15, 60))
    ]
```

### When Mock Data is Used

The system falls back to mock data when:
- `GA_VIEW_ID` environment variable is not set
- Google Analytics API credentials are missing
- API requests fail or timeout

```python
try:
    if self.ga_view_id:
        # Attempt real GA data collection
        self._collect_ga_data()
    else:
        # Fall back to mock data
        self._generate_mock_ga_data()
except Exception as e:
    logging.warning(f"GA data collection failed: {e}")
    self._generate_mock_ga_data()
```

## Setting Up Real Google Analytics

### Step 1: Google Analytics Property Setup

1. **Go to Google Analytics**: https://analytics.google.com/
2. **Create Property** (if not exists):
   - Property name: "NullRecords Website"
   - Website URL: https://nullrecords.com
   - Industry: "Music & Audio"
   - Time zone: Your local timezone

3. **Get View ID**:
   - Navigate to Admin → View → View Settings
   - Copy the "View ID" (numeric value like `12345678`)

### Step 2: Google Cloud Console Setup

1. **Create Project**: https://console.cloud.google.com/
   - Project name: "nullrecords-analytics"
   - Organization: Your organization

2. **Enable Analytics Reporting API**:
   - Navigate to APIs & Services → Library
   - Search for "Google Analytics Reporting API"
   - Click "Enable"

3. **Create Service Account**:
   - Navigate to APIs & Services → Credentials
   - Click "Create Credentials" → "Service Account"
   - Service account name: "nullrecords-analytics-reader"
   - Role: "Viewer"

4. **Download Credentials**:
   - Click on created service account
   - Go to "Keys" tab
   - Click "Add Key" → "Create new key" → "JSON"
   - Download the JSON file
   - **Secure Storage**: Place in `/opt/nullrecords/credentials/ga-service-account.json`

### Step 3: Google Analytics Permissions

1. **Add Service Account to Analytics**:
   - Copy service account email from JSON file
   - In Google Analytics, go to Admin → Account/Property → User Management
   - Add the service account email with "Read & Analyze" permissions

### Step 4: Environment Configuration

**Add to your environment variables**:

```bash
# Production Environment - NullRecords Specific Configuration
export GA_PROPERTY_ID="3376868194"  # Your actual GA Property ID
export GA_MEASUREMENT_ID="G-2WVCJM4NKR"  # Your Measurement ID (already in shared-header.js)
export GOOGLE_APPLICATION_CREDENTIALS="/opt/nullrecords/credentials/ga-service-account.json"

# Development Environment  
export GA_PROPERTY_ID="3376868194"
export GA_MEASUREMENT_ID="G-2WVCJM4NKR"
export GOOGLE_APPLICATION_CREDENTIALS="/Users/greglind/Projects/NullRecords/nullrecords-marketing/ga-credentials.json"
```

**For persistent configuration, add to `.bashrc` or `.zshrc`**:

```bash
echo 'export GA_PROPERTY_ID="3376868194"' >> ~/.zshrc
echo 'export GA_MEASUREMENT_ID="G-2WVCJM4NKR"' >> ~/.zshrc
echo 'export GOOGLE_APPLICATION_CREDENTIALS="/path/to/ga-service-account.json"' >> ~/.zshrc
source ~/.zshrc
```

**Note**: The frontend tracking (gtag.js) is already correctly configured with `G-2WVCJM4NKR` in the shared header system.

### Step 5: Install Required Dependencies

```bash
# Install Google Analytics API client
pip3 install google-analytics-reporting-api google-auth google-auth-oauthlib google-auth-httplib2
```

**Or add to `requirements.txt`**:
```
google-analytics-reporting-api>=0.1.0
google-auth>=2.0.0
google-auth-oauthlib>=0.5.0
google-auth-httplib2>=0.1.0
```

### Step 6: Code Integration

The existing code in `scripts/daily_report.py` should automatically detect the environment variables and switch from mock to real data:

```python
class SystemMetrics:
    def __init__(self):
        self.ga_view_id = os.getenv('GA_VIEW_ID')
        self.ga_credentials = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
        
    def collect_website_metrics(self):
        if self.ga_view_id and self.ga_credentials:
            try:
                self._collect_real_ga_data()
                logging.info("✅ Real Google Analytics data collected")
            except Exception as e:
                logging.warning(f"GA collection failed, using mock data: {e}")
                self._generate_mock_ga_data()
        else:
            logging.info("🔄 Using mock GA data (GA_VIEW_ID not configured)")
            self._generate_mock_ga_data()
```

## Real Data Implementation

### Required Code Updates

**If not already implemented, add to `scripts/daily_report.py`**:

```python
from google.analytics.reporting import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest

def _collect_real_ga_data(self):
    """Collect real Google Analytics data"""
    from google.analytics.reporting import BetaAnalyticsDataClient
    
    client = BetaAnalyticsDataClient()
    
    request = RunReportRequest(
        property=f"properties/{self.ga_view_id}",
        dimensions=[Dimension(name="pagePath")],
        metrics=[
            Metric(name="activeUsers"),
            Metric(name="screenPageViews"), 
            Metric(name="sessions"),
            Metric(name="bounceRate"),
            Metric(name="averageSessionDuration")
        ],
        date_ranges=[DateRange(start_date="yesterday", end_date="yesterday")]
    )
    
    response = client.run_report(request)
    
    # Process response data
    self.metrics.website_visitors = int(response.rows[0].metric_values[0].value)
    self.metrics.website_pageviews = int(response.rows[0].metric_values[1].value)
    self.metrics.website_sessions = int(response.rows[0].metric_values[2].value)
    self.metrics.bounce_rate = float(response.rows[0].metric_values[3].value)
    self.metrics.avg_session_duration = float(response.rows[0].metric_values[4].value)
    
    # Process top pages
    self.metrics.top_pages = [(row.dimension_values[0].value, 
                              int(row.metric_values[1].value)) 
                             for row in response.rows[:5]]
```

## Testing Real Analytics

### Verification Commands

```bash
# Test environment variables
echo "GA_VIEW_ID: $GA_VIEW_ID"
echo "GA_CREDENTIALS: $GOOGLE_APPLICATION_CREDENTIALS"

# Test file exists
ls -la "$GOOGLE_APPLICATION_CREDENTIALS"

# Run daily report to test GA integration
python3 scripts/daily_report.py --test

# Check logs for real vs mock data
tail -f logs/daily_report.log | grep -E "(Real Google Analytics|mock GA data)"
```

### Expected Log Output

**With Real Analytics**:
```
2025-01-12 10:00:00 - INFO - ✅ Real Google Analytics data collected
2025-01-12 10:00:00 - INFO - Website visitors: 287 (real data)
2025-01-12 10:00:00 - INFO - Top pages: [('/', 156), ('/music', 87), ('/about', 44)]
```

**With Mock Analytics**:
```
2025-01-12 10:00:00 - INFO - 🔄 Using mock GA data (GA_VIEW_ID not configured)
2025-01-12 10:00:00 - INFO - Website visitors: 231 (mock data)
2025-01-12 10:00:00 - INFO - Generated mock GA metrics for testing
```

## Troubleshooting

### Common Issues

**1. Permission Denied**
```
Error: The caller does not have permission
```
**Solution**: Ensure service account has "Read & Analyze" permissions in GA

**2. View ID Not Found**
```
Error: View ID 123456789 not found
```
**Solution**: Verify View ID in GA Admin → View Settings

**3. Credentials File Missing**
```
Error: Could not load credentials file
```
**Solution**: Check file path and permissions:
```bash
chmod 600 /path/to/ga-service-account.json
```

**4. API Not Enabled**
```
Error: Google Analytics Reporting API has not been used
```
**Solution**: Enable API in Google Cloud Console

### Debug Mode

**Test GA connection directly**:

```python
# Test script: test_ga_connection.py
import os
from google.analytics.reporting import BetaAnalyticsDataClient

try:
    ga_view_id = os.getenv('GA_VIEW_ID')
    credentials_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
    
    print(f"GA View ID: {ga_view_id}")
    print(f"Credentials: {credentials_path}")
    
    client = BetaAnalyticsDataClient()
    print("✅ Analytics client created successfully")
    
    # Test simple query
    # Add test query here
    
except Exception as e:
    print(f"❌ GA connection failed: {e}")
```

## Production Deployment

### Security Checklist

- [ ] Service account JSON file secured (600 permissions)
- [ ] Environment variables set in production environment
- [ ] GA service account has minimal required permissions
- [ ] Credentials file path accessible to application user
- [ ] Backup credentials stored securely
- [ ] Regular audit of GA access logs

### Monitoring

**Add to daily report monitoring**:
```python
# Monitor data source in daily reports
if self.using_mock_data:
    logging.warning("📊 Daily report using MOCK analytics data")
    # Optional: Send alert to admin
else:
    logging.info("📊 Daily report using REAL analytics data")
```

## Cost Considerations

- **Google Analytics**: Free for standard reporting
- **Analytics Reporting API**: Free for up to 50,000 requests/day
- **Current Usage**: ~3 requests/day (daily reports)
- **Estimated Cost**: $0/month

## Migration Timeline

1. **Phase 1**: Set up GA property and API access (1 hour)
2. **Phase 2**: Configure service account and credentials (30 minutes)  
3. **Phase 3**: Update environment variables (15 minutes)
4. **Phase 4**: Test real data collection (30 minutes)
5. **Phase 5**: Deploy to production (15 minutes)

**Total Estimated Time**: 2.5 hours

Once configured, the system will automatically use real Google Analytics data instead of mock data for all daily reports and analytics.
