# NullRecords Development Guide

This guide provides an overview of the NullRecords Marketing project structure, development workflow, and automation systems.

## 📁 Project Structure

```
nullrecords-marketing/
├── devdocs/                    # Development documentation
├── scripts/                    # Automation and utility scripts
├── .github/prompts/           # Development guides and prompts
├── assets/                    # Static assets (CSS, JS, images)
├── src/                       # Source code
├── news/                      # Generated news content
├── home/                      # Django home app
├── mysite/                    # Django project settings
├── search/                    # Django search app
└── static/                    # Compiled static files
```

## 📚 Documentation (devdocs/)

All development documentation is organized in the `devdocs/` folder:

- **[AUTOMATION_SETUP.md](../../devdocs/AUTOMATION_SETUP.md)** - Daily automation system setup
- **[DAILY_REPORT_SETUP.md](../../devdocs/DAILY_REPORT_SETUP.md)** - Analytics reporting system
- **[ENHANCED_NEWS_SYSTEM.md](../../devdocs/ENHANCED_NEWS_SYSTEM.md)** - News monitoring and content generation
- **[ENVIRONMENT_SETUP.md](../../devdocs/ENVIRONMENT_SETUP.md)** - Environment configuration guide
- **[GITHUB_PAGES_FIXES.md](../../devdocs/GITHUB_PAGES_FIXES.md)** - Deployment and hosting fixes
- **[INTERACTIVE_GUIDE.md](../../devdocs/INTERACTIVE_GUIDE.md)** - Interactive outreach system
- **[NEWS_SYSTEM_COMPLETE.md](../../devdocs/NEWS_SYSTEM_COMPLETE.md)** - Complete news system documentation
- **[OUTREACH_README.md](../../devdocs/OUTREACH_README.md)** - Music industry outreach system
- **[SECURITY_COMPLETE.md](../../devdocs/SECURITY_COMPLETE.md)** - Security implementation guide
- **[ASSETS_RESTRUCTURE_COMPLETE.md](../../devdocs/ASSETS_RESTRUCTURE_COMPLETE.md)** - Asset organization
- **[IMAGE_FIXES_COMPLETE.md](../../devdocs/IMAGE_FIXES_COMPLETE.md)** - Image handling improvements
- **[SECURITY_IMPLEMENTATION.md](../../devdocs/SECURITY_IMPLEMENTATION.md)** - Security best practices

## 🔧 Scripts (scripts/)

All automation and utility scripts are organized in the `scripts/` folder:

### Python Scripts
- **`music_outreach.py`** - Music industry outreach automation
- **`daily_report.py`** - Daily analytics report generation
- **`news_monitor.py`** - News monitoring and content generation
- **`google_sheets_voting.py`** - Google Sheets voting system integration
- **`validate_env.py`** - Environment validation and setup verification

### Shell Scripts
- **`setup_cron.sh`** - Automated cron job setup and management
- **`daily_report_system.sh`** - Daily report system runner
- **`news_system.sh`** - News monitoring system runner
- **`monitor_cron.sh`** - Cron job monitoring and status checker
- **`daily_outreach.sh`** - Daily outreach automation
- **`interactive_outreach.sh`** - Interactive outreach interface

## 🚀 Getting Started

### 1. Environment Setup
```bash
# Copy environment template
cp .env.template .env

# Edit with your credentials
nano .env

# Validate configuration
python3 scripts/validate_env.py
```

### 2. Install Dependencies
```bash
# Python dependencies
pip install -r requirements.txt

# Node.js dependencies (for CSS builds)
npm install
```

### 3. Test Systems
```bash
# Test outreach system
python3 scripts/music_outreach.py --daily --limit 1

# Test daily report
./scripts/daily_report_system.sh generate

# Test news monitoring
./scripts/news_system.sh collect
```

### 4. Setup Automation
```bash
# Install automated cron jobs
./scripts/setup_cron.sh install

# Monitor automation
./scripts/monitor_cron.sh
```

## 🔄 Daily Operations

The system runs automated daily operations:

- **6:00 AM** - News collection starts
- **7:00 AM** - Generate news pages
- **7:30 AM** - Update main site
- **8:00 AM** - Send daily report email
- **8:30 AM** - Deploy to GitHub Pages
- **9:00 AM** - Music outreach campaign
- **10:00 AM & 8:00 PM** - Release monitoring

## 🛠️ Development Workflow

### 1. Making Changes
```bash
# Create feature branch
git checkout -b feature/new-feature

# Make changes
# Test changes locally

# Commit and push
git add .
git commit -m "feat: description of changes"
git push origin feature/new-feature
```

### 2. Testing
```bash
# Validate environment
python3 scripts/validate_env.py

# Test specific systems
./scripts/daily_report_system.sh test
./scripts/news_system.sh test

# Check cron status
./scripts/monitor_cron.sh
```

### 3. Deployment
The system automatically deploys via GitHub Pages through:
- Automated daily commits (8:30 AM)
- Manual deployments via git push to main branch

## 📊 Monitoring

- **Cron Logs**: `~/nullrecords_cron.log`
- **System Status**: `./scripts/monitor_cron.sh`
- **Daily Reports**: Emailed daily at 8:00 AM
- **News Updates**: Generated and deployed automatically

## 🔐 Security

- All credentials stored in `.env` file (not committed)
- Environment validation prevents accidental exposure
- Regular security audits via `scripts/validate_env.py`
- Secure SMTP and API configurations

## 📞 Support

For development questions or issues:
1. Check relevant documentation in `devdocs/`
2. Review logs with `./scripts/monitor_cron.sh`
3. Validate environment with `python3 scripts/validate_env.py`
4. Test individual systems manually

## 🔄 System Updates

To update the automation system:
```bash
# Remove old cron jobs
./scripts/setup_cron.sh remove

# Make changes to scripts
# Update documentation

# Reinstall cron jobs
./scripts/setup_cron.sh install

# Monitor new setup
./scripts/monitor_cron.sh
```
