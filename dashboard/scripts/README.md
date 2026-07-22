# NullRecords Scripts Directory

This directory contains all automation and utility scripts for the NullRecords Marketing project.

## 📁 Script Organization

### 🐍 Python Scripts

#### Core Automation
- **`music_outreach.py`** - Music industry outreach automation system
  - Discovers new contacts across platforms
  - Sends personalized outreach emails
  - Tracks responses and maintains contact database
  - Usage: `python3 scripts/music_outreach.py --daily --limit 5`

- **`daily_report.py`** - Comprehensive daily analytics reporting
  - Google Analytics integration
  - YouTube metrics collection
  - Email campaign statistics
  - Google Sheets voting data
  - Usage: `python3 scripts/daily_report.py --send-email`

- **`news_monitor.py`** - News monitoring and content generation
  - RSS feed monitoring
  - Streaming platform release tracking
  - Automated news page generation
  - Email notifications for new content
  - Usage: `python3 scripts/news_monitor.py --collect`

#### Utilities
- **`google_sheets_voting.py`** - Google Sheets voting system integration
  - Connects to voting spreadsheets
  - Parses and validates voting data
  - Provides mock data for testing
  - Usage: Imported by `daily_report.py`

- **`validate_env.py`** - Environment validation and security verification
  - Checks all required environment variables
  - Tests API connections
  - Validates SMTP configuration
  - Detects hardcoded credentials
  - Usage: `python3 scripts/validate_env.py`

### 🔧 Shell Scripts

#### System Management
- **`setup_cron.sh`** - Automated cron job setup and management
  - Installs daily automation schedule
  - Backs up existing crontab
  - Provides system monitoring tools
  - Usage: `./scripts/setup_cron.sh install`

- **`monitor_cron.sh`** - Cron job monitoring and status checking
  - Shows recent log entries
  - Displays active cron jobs
  - Checks system health
  - Usage: `./scripts/monitor_cron.sh`

#### Service Runners
- **`daily_report_system.sh`** - Daily report system runner
  - Generates reports manually or via cron
  - Sends email reports
  - Provides historical report generation
  - Usage: `./scripts/daily_report_system.sh email`

- **`news_system.sh`** - News monitoring system runner
  - Collects news from RSS feeds
  - Monitors streaming platform releases
  - Generates news pages
  - Updates main site
  - Usage: `./scripts/news_system.sh collect`

#### Interactive Tools
- **`daily_outreach.sh`** - Daily outreach automation wrapper
  - Runs outreach campaigns
  - Provides logging and notifications
  - Handles both interactive and automated modes
  - Usage: `./scripts/daily_outreach.sh`

- **`interactive_outreach.sh`** - Interactive outreach interface
  - Manual outreach with email preview
  - Step-by-step guided process
  - Real-time contact discovery
  - Usage: `./scripts/interactive_outreach.sh`

## 🚀 Quick Usage Guide

### Daily Operations
```bash
# Run daily outreach (5 contacts)
python3 scripts/music_outreach.py --daily --limit 5

# Generate and send daily report
./scripts/daily_report_system.sh email

# Collect latest news
./scripts/news_system.sh collect

# Check system status
./scripts/monitor_cron.sh
```

### Setup and Configuration
```bash
# Validate environment setup
python3 scripts/validate_env.py

# Install automated cron jobs
./scripts/setup_cron.sh install

# Test all systems
./scripts/daily_report_system.sh test
./scripts/news_system.sh test
```

### Interactive Mode
```bash
# Interactive outreach with email preview
./scripts/interactive_outreach.sh

# Manual report generation
./scripts/daily_report_system.sh generate
```

## 🔄 Automation Schedule

When installed via `setup_cron.sh`, the following schedule runs automatically:

- **6:00 AM** - News collection (`news_system.sh collect`)
- **7:00 AM** - Generate news pages (`news_system.sh generate`)
- **7:30 AM** - Update main site (`news_system.sh update`)
- **8:00 AM** - Send daily report (`daily_report_system.sh email`)
- **8:30 AM** - Deploy to GitHub Pages (git push)
- **9:00 AM** - Music outreach campaign (`music_outreach.py --daily`)
- **10:00 AM & 8:00 PM** - Release monitoring (`news_system.sh releases`)
- **Sunday 6:00 PM** - Weekly comprehensive report (`news_system.sh report`)

## 📋 Environment Requirements

All scripts require:
- **Python 3.7+** with packages from `../requirements.txt`
- **Bash shell** (macOS/Linux compatible)
- **Environment file** (`.env`) with required API keys and credentials
- **Working directory** must be project root (scripts handle this automatically)

## 🔐 Security Notes

- **All scripts validate environment** before running
- **No hardcoded credentials** - everything uses `.env` file
- **Secure API handling** with proper error handling
- **Log rotation** and cleanup to prevent disk space issues

## 🐛 Troubleshooting

### Common Issues
1. **"Script not found"** - Run from project root, not scripts directory
2. **"Environment validation failed"** - Run `python3 scripts/validate_env.py`
3. **"Cron jobs not running"** - Check `~/nullrecords_cron.log`
4. **"Permission denied"** - Make scripts executable: `chmod +x scripts/*.sh`

### Debugging
```bash
# Check environment
python3 scripts/validate_env.py

# Test individual components
python3 scripts/music_outreach.py --test
python3 scripts/daily_report.py --test

# Monitor logs
tail -f ~/nullrecords_cron.log

# Check cron status
./scripts/monitor_cron.sh
```

## 📊 Output and Logs

- **Cron logs**: `~/nullrecords_cron.log`
- **Daily reports**: `daily_report_YYYY-MM-DD.html`
- **Outreach logs**: `outreach.log`
- **News monitoring**: `news_monitor.log`

## 🔧 Development

When modifying scripts:

1. **Test thoroughly** in development environment
2. **Update documentation** in `../devdocs/`
3. **Validate security** with `validate_env.py`
4. **Check cron compatibility** if adding new automated features
5. **Update main development guide** in `../.github/prompts/`

## 📞 Support

For script-related issues:
1. **Run validation**: `python3 scripts/validate_env.py`
2. **Check logs**: `./scripts/monitor_cron.sh`
3. **Test manually**: Run scripts with `--test` or `--help` flags
4. **Review documentation**: See `../devdocs/` for detailed guides
