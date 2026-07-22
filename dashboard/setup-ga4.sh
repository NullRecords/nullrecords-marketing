#!/bin/bash

# NullRecords Google Analytics GA4 Setup Script
# =============================================

echo "🎵 NullRecords - Google Analytics GA4 Setup"
echo "============================================="
echo ""

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if we're in the right directory
if [ ! -f "shared-header.js" ] || [ ! -f "scripts/daily_report.py" ]; then
    print_error "This script must be run from the NullRecords Marketing directory"
    exit 1
fi

print_status "Installing Google Analytics GA4 dependencies..."

# Install Python dependencies
if command -v pip3 &> /dev/null; then
    pip3 install google-analytics-data>=0.18.0 google-auth>=2.0.0 google-auth-oauthlib>=0.5.0 google-auth-httplib2>=0.1.0 google-api-python-client>=2.0.0
    print_success "Python dependencies installed"
else
    print_error "pip3 not found. Please install pip3 first."
    exit 1
fi

# Check current environment configuration
print_status "Checking current environment configuration..."

if [ -f ".env" ]; then
    print_success "Found .env file"
    
    # Check for GA4 configuration
    if grep -q "GA_PROPERTY_ID" .env; then
        print_success "GA_PROPERTY_ID found in .env"
    else
        print_warning "GA_PROPERTY_ID not found in .env"
        echo "Adding GA4 configuration to .env..."
        echo "" >> .env
        echo "# Google Analytics GA4 Configuration" >> .env
        echo "GA_PROPERTY_ID=3376868194" >> .env
        echo "GA_MEASUREMENT_ID=G-2WVCJM4NKR" >> .env
        echo "GOOGLE_APPLICATION_CREDENTIALS=/path/to/your/ga4-service-account.json" >> .env
        print_success "GA4 configuration added to .env"
    fi
else
    print_warning ".env file not found. Creating from template..."
    cp .env.template .env
    print_success ".env file created from template"
fi

print_status "Current GA4 configuration:"
echo "------------------------"
echo "Property ID: 3376868194"
echo "Measurement ID: G-2WVCJM4NKR (already configured in shared-header.js)"
echo "Stream URL: https://www.nullrecords.com"
echo ""

print_status "Next steps to enable real analytics data:"
echo "1. Set up Google Cloud Project and enable Analytics Data API"
echo "2. Create service account with Analytics Reader permissions"
echo "3. Download service account JSON key file"
echo "4. Update GOOGLE_APPLICATION_CREDENTIALS in .env to point to your JSON file"
echo "5. Add service account email to Google Analytics property permissions"
echo ""

print_status "Testing current configuration..."

# Test environment variables
python3 -c "
import os
print('Current environment:')
print(f'  GA_PROPERTY_ID: {os.getenv(\"GA_PROPERTY_ID\", \"Not set\")}')
print(f'  GA_MEASUREMENT_ID: {os.getenv(\"GA_MEASUREMENT_ID\", \"Not set\")}')
print(f'  GOOGLE_APPLICATION_CREDENTIALS: {os.getenv(\"GOOGLE_APPLICATION_CREDENTIALS\", \"Not set\")}')
print()

# Test if credentials file exists
creds_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
if creds_path and os.path.exists(creds_path):
    print('✅ Service account credentials file found')
else:
    print('❌ Service account credentials file not found or not configured')
    print('   Please download your GA4 service account JSON file and update')
    print('   GOOGLE_APPLICATION_CREDENTIALS in your .env file')
"

echo ""
print_status "Running daily report test..."

# Run daily report to test
python3 scripts/daily_report.py 2>&1 | tail -5

echo ""
print_success "Setup complete! Check the output above for any issues."
print_status "Frontend tracking is already active via shared-header.js"
print_status "Backend reporting will use real data once service account is configured"

echo ""
print_status "Documentation:"
echo "- Shared Header System: devdocs/SHARED_HEADER_SYSTEM.md"
echo "- Google Analytics Setup: devdocs/GOOGLE_ANALYTICS_SETUP.md"
