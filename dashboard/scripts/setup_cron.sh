#!/bin/bash

# NullRecords Automated Cron Setup
# ================================

set -e  # Exit on any error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

print_header() {
    echo -e "${CYAN}⏰ NullRecords Automated Operations Setup${NC}"
    echo -e "${CYAN}=========================================${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

print_info() {
    echo -e "${YELLOW}ℹ️  $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

check_prerequisites() {
    print_info "Checking prerequisites..."
    
    # Check if we're in the right directory
    if [[ ! -f "scripts/music_outreach.py" ]]; then
        print_error "scripts/music_outreach.py not found - make sure you're in the NullRecords project directory"
        exit 1
    fi
    
    # Check if .env file exists
    if [[ ! -f ".env" ]]; then
        print_error ".env file not found - please create one with your credentials"
        exit 1
    fi
    
    # Test systems quickly
    print_info "Testing systems..."
    
    # Test validation
    if ! python3 scripts/validate_env.py > /dev/null 2>&1; then
        print_error "Environment validation failed - run: python3 scripts/validate_env.py"
        exit 1
    fi
    
    print_success "Prerequisites checked"
}

backup_existing_crontab() {
    print_info "Backing up existing crontab..."
    
    if crontab -l > /dev/null 2>&1; then
        crontab -l > "crontab_backup_$(date +%Y%m%d_%H%M%S).txt"
        print_success "Existing crontab backed up"
    else
        print_info "No existing crontab found"
    fi
}

install_cron_jobs() {
    print_info "Installing NullRecords cron jobs..."
    
    # Update paths in cron file to use project directory
    sed "s|/Users/greglind/Projects/NullRecords/nullrecords-marketing|$PROJECT_DIR|g" cron_schedule.txt > temp_cron.txt
    
    # Get current crontab (if any)
    if crontab -l > /dev/null 2>&1; then
        current_cron=$(mktemp)
        crontab -l > "$current_cron"
        
        # Remove any existing NullRecords entries
        grep -v "NullRecords\\|nullrecords\\|music_outreach\\|daily_report\\|news_system" "$current_cron" > temp_existing_cron.txt || true
        
        # Combine existing (cleaned) + new cron entries
        cat temp_existing_cron.txt temp_cron.txt | crontab -
        
        rm -f "$current_cron" temp_existing_cron.txt
    else
        # No existing crontab, just install ours
        crontab temp_cron.txt
    fi
    
    rm -f temp_cron.txt
    
    print_success "Cron jobs installed"
}

show_installed_jobs() {
    print_info "Installed cron jobs:"
    echo ""
    
    # Show NullRecords related cron jobs
    crontab -l | grep -E "(NullRecords|nullrecords|music_outreach|daily_report|news_system)" || echo "No NullRecords cron jobs found"
    
    echo ""
    print_info "Cron log location: ~/nullrecords_cron.log"
}

test_cron_environment() {
    print_info "Testing cron environment..."
    
    # Create a test cron job that runs in 1 minute (handle leading zeros properly)
    current_minute=$(date +%M | sed 's/^0*//')
    current_hour=$(date +%H | sed 's/^0*//')
    
    # Handle empty strings (when minute/hour is 00)
    [ -z "$current_minute" ] && current_minute=0
    [ -z "$current_hour" ] && current_hour=0
    
    next_minute=$((current_minute + 1))
    if [ $next_minute -eq 60 ]; then
        next_minute=0
        test_hour=$((current_hour + 1))
        if [ $test_hour -eq 24 ]; then
            test_hour=0
        fi
    else
        test_hour=$current_hour
    fi
    
    test_job="$next_minute $test_hour * * * cd $SCRIPT_DIR && echo 'Cron test successful at \$(date)' >> ~/nullrecords_cron_test.log"
    
    # Add test job
    (crontab -l 2>/dev/null; echo "$test_job") | crontab -
    
    print_info "Test cron job scheduled for next minute ($next_minute:$test_hour)"
    print_info "Check ~/nullrecords_cron_test.log in 2 minutes to verify it worked"
    
    # Schedule cleanup of test job in 5 minutes
    cleanup_minute=$((current_minute + 5))
    cleanup_hour=$current_hour
    if [ $cleanup_minute -ge 60 ]; then
        cleanup_minute=$((cleanup_minute - 60))
        cleanup_hour=$((current_hour + 1))
        if [ $cleanup_hour -eq 24 ]; then
            cleanup_hour=0
        fi
    fi
    
    cleanup_job="$cleanup_minute $cleanup_hour * * * crontab -l | grep -v 'nullrecords_cron_test' | crontab -"
    
    (crontab -l 2>/dev/null; echo "$cleanup_job") | crontab -
}

create_monitoring_script() {
    print_info "Creating cron monitoring script..."
    
    cat > monitor_cron.sh << 'EOF'
#!/bin/bash
# NullRecords Cron Job Monitor

echo "🔍 NullRecords Cron Job Status - $(date)"
echo "========================================"

# Check if cron service is running
if pgrep cron > /dev/null; then
    echo "✅ Cron service is running"
else
    echo "❌ Cron service is not running"
fi

# Show recent log entries
if [[ -f ~/nullrecords_cron.log ]]; then
    echo ""
    echo "📋 Recent Log Entries (last 20 lines):"
    tail -20 ~/nullrecords_cron.log
else
    echo "⚠️  No cron log file found at ~/nullrecords_cron.log"
fi

# Show active NullRecords cron jobs
echo ""
echo "⏰ Active NullRecords Cron Jobs:"
crontab -l | grep -E "(music_outreach|daily_report|news_system)" || echo "No NullRecords cron jobs found"

# Check disk space
echo ""
echo "💾 Disk Space:"
df -h . | tail -1

# Check last git commit
echo ""
echo "📦 Last Git Commit:"
git log -1 --oneline 2>/dev/null || echo "No git repository"

EOF

    chmod +x monitor_cron.sh
    print_success "Created monitor_cron.sh - run this to check cron job status"
}

print_usage_instructions() {
    echo ""
    print_info "📚 Usage Instructions:"
    echo ""
    echo "Manual Operations:"
    echo "  ./daily_report_system.sh email    # Send daily report now"
    echo "  python3 scripts/music_outreach.py --daily # Run outreach campaign now"  
    echo "  ./news_system.sh full             # Complete news update now"
    echo ""
    echo "Monitoring:"
    echo "  ./monitor_cron.sh                 # Check cron job status"
    echo "  tail -f ~/nullrecords_cron.log    # Watch live cron output"
    echo "  crontab -l                        # List all cron jobs"
    echo ""
    echo "Maintenance:"
    echo "  crontab -e                        # Edit cron jobs"
    echo "  ./setup_cron.sh remove            # Remove NullRecords cron jobs"
    echo ""
}

remove_cron_jobs() {
    print_info "Removing NullRecords cron jobs..."
    
    if crontab -l > /dev/null 2>&1; then
        crontab -l | grep -v -E "(NullRecords|nullrecords|music_outreach|daily_report|news_system)" | crontab -
        print_success "NullRecords cron jobs removed"
    else
        print_info "No crontab found"
    fi
}

show_schedule() {
    print_info "📅 NullRecords Automated Schedule:"
    echo ""
    cat << 'EOF'
⏰ DAILY SCHEDULE:
  06:00 - News collection (every 6h: 6,12,18,0)
  07:00 - Generate news pages
  07:30 - Update main site  
  08:00 - Send daily report email
  08:30 - Deploy to GitHub Pages
  09:00 - Music outreach campaign
  10:00 - Release monitoring
  20:00 - Evening release monitoring

📅 WEEKLY SCHEDULE:
  Sunday 18:00 - Comprehensive weekly report

🗂️  MONTHLY SCHEDULE:
  1st day 02:00 - Archive and cleanup old logs

📊 MONITORING:
  - All output logged to ~/nullrecords_cron.log
  - Use ./monitor_cron.sh to check status
  - Email notifications for all activities
EOF
    echo ""
}

# Main execution
case "${1:-install}" in
    "install")
        print_header
        check_prerequisites
        backup_existing_crontab
        install_cron_jobs
        show_installed_jobs
        create_monitoring_script
        test_cron_environment
        show_schedule
        print_usage_instructions
        print_success "NullRecords automation setup complete! 🎉"
        ;;
    "remove")
        print_header
        backup_existing_crontab
        remove_cron_jobs
        print_success "NullRecords cron jobs removed"
        ;;
    "status")
        print_header
        show_installed_jobs
        ;;
    "schedule")
        print_header
        show_schedule
        ;;
    "test")
        print_header
        check_prerequisites
        test_cron_environment
        ;;
    "help"|*)
        print_header
        echo ""
        echo "Usage: $0 [command]"
        echo ""
        echo "Commands:"
        echo "  install   - Install NullRecords automated cron jobs (default)"
        echo "  remove    - Remove all NullRecords cron jobs"
        echo "  status    - Show current cron job status" 
        echo "  schedule  - Show automation schedule"
        echo "  test      - Test cron environment"
        echo "  help      - Show this help message"
        echo ""
        ;;
esac
