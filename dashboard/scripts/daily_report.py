#!/usr/bin/env python3
"""
NullRecords Daily Status Report System
=====================================

Comprehensive daily reporting system that collects and analyzes:
- Google Analytics traffic data
- YouTube channel metrics
- Email campaign statistics
- Google Sheets voting data
- News monitoring results
- System health metrics

Usage: python daily_report.py [--send-email] [--date YYYY-MM-DD]
"""

import json
import os
import smtplib
import time
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, List, Optional, Any
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
import argparse

SCRIPT_DIR = Path(__file__).resolve().parent
DASHBOARD_DIR = SCRIPT_DIR.parent
WORKSPACE_DIR = DASHBOARD_DIR.parent
LOG_DIR = DASHBOARD_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Import opt-out management
try:
    from email_opt_out import check_opt_out, get_opt_out_link
    OPT_OUT_AVAILABLE = True
except ImportError:
    OPT_OUT_AVAILABLE = False
    logging.warning("⚠️  Email opt-out system not available")

# Load environment variables
try:
    from dotenv import load_dotenv
    # Try to find .env file in current directory or parent directory
    env_paths = ['.env', '../.env', os.path.join(os.path.dirname(__file__), '..', '.env')]
    env_loaded = False
    for env_path in env_paths:
        if os.path.exists(env_path):
            load_dotenv(env_path)
            logging.info(f"✅ Environment variables loaded from {env_path}")
            env_loaded = True
            break
    
    # If not found in relative paths, try absolute path to workspace root
    if not env_loaded:
        workspace_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        env_path = os.path.join(workspace_root, '.env')
        if os.path.exists(env_path):
            load_dotenv(env_path)
            logging.info(f"✅ Environment variables loaded from {env_path}")
            env_loaded = True
    
    if not env_loaded:
        logging.warning("⚠️  .env file not found in expected locations")
except ImportError:
    logging.warning("⚠️  python-dotenv not installed - using system environment variables only")

# Optional dependencies
try:
    import requests
    from bs4 import BeautifulSoup
    SCRAPING_AVAILABLE = True
except ImportError:
    SCRAPING_AVAILABLE = False
    logging.warning("⚠️  Scraping libraries not available")

# Google APIs (optional)
try:
    from googleapiclient.discovery import build
    from google.oauth2 import service_account
    GOOGLE_APIS_AVAILABLE = True
except ImportError:
    GOOGLE_APIS_AVAILABLE = False
    logging.warning("⚠️  Google API libraries not available - install google-api-python-client google-auth")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / 'daily_report.log'),
        logging.StreamHandler()
    ]
)


def get_ai_engine_url() -> str:
    """Return configured AI Engine URL, falling back to the service manager port file."""
    configured = os.getenv('AI_ENGINE_URL')
    if configured:
        return configured.rstrip("/")

    port_file = WORKSPACE_DIR / "ops" / ".pids" / "ai-engine.port"
    if port_file.exists():
        port = port_file.read_text().strip()
        if port:
            return f"http://localhost:{port}"

    return "http://localhost:8008"

@dataclass
class DailyMetrics:
    """Daily metrics data structure"""
    date: str
    
    # Website Analytics
    website_visitors: int = 0
    website_pageviews: int = 0
    website_sessions: int = 0
    bounce_rate: float = 0.0
    avg_session_duration: float = 0.0
    top_pages: List[Dict] = None
    traffic_sources: Dict[str, int] = None
    
    # YouTube Metrics
    youtube_views: int = 0
    youtube_subscribers: int = 0
    youtube_watch_time: float = 0.0
    youtube_new_videos: int = 0
    top_videos: List[Dict] = None
    
    # Email Campaign Metrics
    emails_sent: int = 0
    email_open_rate: float = 0.0
    email_click_rate: float = 0.0
    outreach_campaigns: int = 0
    
    # Outreach Metrics
    outreach_total_contacts: int = 0
    outreach_emails_sent_today: int = 0
    outreach_status: Dict[str, int] = None
    outreach_new_sources: List[Dict] = None
    outreach_responses: List[Dict] = None
    
    # Voting & Engagement
    new_votes: int = 0
    total_votes: int = 0
    voting_trends: Dict[str, int] = None
    
    # News & Content Monitoring
    new_articles: int = 0
    new_releases: int = 0
    monitoring_sources: int = 0
    content_sentiment: Dict[str, int] = None
    
    # System Health
    system_uptime: float = 100.0
    api_response_times: Dict[str, float] = None
    error_count: int = 0
    
    def __post_init__(self):
        if self.top_pages is None:
            self.top_pages = []
        if self.traffic_sources is None:
            self.traffic_sources = {}
        if self.top_videos is None:
            self.top_videos = []
        if self.voting_trends is None:
            self.voting_trends = {}
        if self.content_sentiment is None:
            self.content_sentiment = {}
        if self.api_response_times is None:
            self.api_response_times = {}

class DailyReportSystem:
    """Main daily reporting system"""
    
    def __init__(self):
        self.report_date = datetime.now().strftime('%Y-%m-%d')
        self.metrics = DailyMetrics(date=self.report_date)
        self.ai_engine_url = get_ai_engine_url()
        self.initialize_apis()
        
    def initialize_apis(self):
        """Initialize API connections"""
        # Google Analytics (GA4 Data API)
        self.ga_service = None
        if GOOGLE_APIS_AVAILABLE:
            try:
                credentials_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
                property_id = os.getenv('GA_PROPERTY_ID')
                
                if credentials_path and os.path.exists(credentials_path) and property_id:
                    from google.analytics.data_v1beta import BetaAnalyticsDataClient
                    credentials = service_account.Credentials.from_service_account_file(
                        credentials_path,
                        scopes=['https://www.googleapis.com/auth/analytics.readonly']
                    )
                    self.ga_service = BetaAnalyticsDataClient(credentials=credentials)
                    self.ga_property_id = f"properties/{property_id}"
                    logging.info(f"✅ Google Analytics GA4 API initialized with property: {self.ga_property_id}")
                    logging.info(f"✅ Using credentials: {credentials_path}")
                else:
                    logging.warning(f"⚠️  GA4 credentials or property ID not configured - path: {credentials_path}, property: {property_id}")
            except Exception as e:
                logging.error(f"❌ Failed to initialize Google Analytics: {e}")
        else:
            logging.warning("⚠️  Google APIs not available")
        
        # YouTube API
        self.youtube_service = None
        if GOOGLE_APIS_AVAILABLE:
            try:
                # Try to use the same service account credentials for YouTube API
                credentials_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
                if credentials_path and os.path.exists(credentials_path):
                    credentials = service_account.Credentials.from_service_account_file(
                        credentials_path,
                        scopes=['https://www.googleapis.com/auth/youtube.readonly']
                    )
                    self.youtube_service = build('youtube', 'v3', credentials=credentials)
                    logging.info("✅ YouTube API initialized with service account")
                else:
                    # Fallback to API key if available
                    youtube_api_key = os.getenv('YOUTUBE_API_KEY')
                    if youtube_api_key:
                        self.youtube_service = build('youtube', 'v3', developerKey=youtube_api_key)
                        logging.info("✅ YouTube API initialized with API key")
            except Exception as e:
                logging.error(f"❌ Failed to initialize YouTube API: {e}")
                logging.warning("⚠️  YouTube API not available - using mock data")
    
    def collect_google_analytics_data(self):
        """Collect Google Analytics data"""
        logging.info("📊 Collecting Google Analytics data...")
        
        if not self.ga_service:
            logging.warning("⚠️  Google Analytics not available - using mock data")
            self._generate_mock_ga_data()
            return
        
        try:
            # Check for GA4 configuration first
            property_id = os.getenv('GA_PROPERTY_ID')
            
            if property_id and self.ga_service and hasattr(self, 'ga_property_id'):
                self._collect_ga4_data()
                return
            
            # Fallback to legacy GA configuration
            view_id = os.getenv('GA_VIEW_ID')
            if not view_id:
                logging.error("❌ Neither GA_PROPERTY_ID nor GA_VIEW_ID set in environment variables")
                self._generate_mock_ga_data()
                return
            
            # Define date range (yesterday)
            yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
            
            # Build request
            request = {
                'reportRequests': [
                    {
                        'viewId': view_id,
                        'dateRanges': [{'startDate': yesterday, 'endDate': yesterday}],
                        'metrics': [
                            {'expression': 'ga:users'},
                            {'expression': 'ga:pageviews'},
                            {'expression': 'ga:sessions'},
                            {'expression': 'ga:bounceRate'},
                            {'expression': 'ga:avgSessionDuration'}
                        ],
                        'dimensions': [
                            {'name': 'ga:pagePath'},
                            {'name': 'ga:source'}
                        ]
                    }
                ]
            }
            
            # Execute request
            response = self.ga_service.reports().batchGet(body=request).execute()
            
            # Parse response
            for report in response.get('reports', []):
                data = report.get('data', {})
                totals = data.get('totals', [{}])[0]
                
                if totals.get('values'):
                    self.metrics.website_visitors = int(totals['values'][0])
                    self.metrics.website_pageviews = int(totals['values'][1])
                    self.metrics.website_sessions = int(totals['values'][2])
                    self.metrics.bounce_rate = float(totals['values'][3])
                    self.metrics.avg_session_duration = float(totals['values'][4])
                
                # Extract top pages and traffic sources
                rows = data.get('rows', [])
                page_views = {}
                traffic_sources = {}
                
                for row in rows:
                    dimensions = row.get('dimensions', [])
                    metrics = row.get('metrics', [{}])[0].get('values', [])
                    
                    if len(dimensions) >= 2:
                        page = dimensions[0]
                        source = dimensions[1]
                        pageviews = int(metrics[1]) if len(metrics) > 1 else 0
                        
                        page_views[page] = page_views.get(page, 0) + pageviews
                        traffic_sources[source] = traffic_sources.get(source, 0) + pageviews
                
                # Sort and store top pages
                self.metrics.top_pages = [
                    {'page': page, 'views': views}
                    for page, views in sorted(page_views.items(), key=lambda x: x[1], reverse=True)[:5]
                ]
                
                self.metrics.traffic_sources = dict(
                    sorted(traffic_sources.items(), key=lambda x: x[1], reverse=True)[:5]
                )
            
            logging.info(f"✅ GA Data: {self.metrics.website_visitors} visitors, {self.metrics.website_pageviews} pageviews")
            
        except Exception as e:
            logging.error(f"❌ Error collecting Google Analytics data: {e}")
            self._generate_mock_ga_data()
    
    def _generate_mock_ga_data(self):
        """Set Google Analytics fields to zero / empty when the API is unavailable."""
        logging.info("ℹ️  GA data unavailable — report will show zeroes for analytics")
        self.metrics.website_visitors = 0
        self.metrics.website_pageviews = 0
        self.metrics.website_sessions = 0
        self.metrics.bounce_rate = 0.0
        self.metrics.avg_session_duration = 0.0
        self.metrics.top_pages = []
        self.metrics.traffic_sources = {}
    
    def _collect_ga4_data(self):
        """Collect Google Analytics GA4 data using the Data API v1 Beta"""
        logging.info("📊 Collecting GA4 data...")
        
        try:
            from google.analytics.data_v1beta.types import (
                DateRange, Dimension, Metric, RunReportRequest
            )
            
            # Define date range (yesterday)
            yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
            
            # Basic metrics request
            request = RunReportRequest(
                property=self.ga_property_id,
                dimensions=[
                    Dimension(name="pagePath"),
                    Dimension(name="sessionDefaultChannelGrouping")
                ],
                metrics=[
                    Metric(name="activeUsers"),
                    Metric(name="screenPageViews"),
                    Metric(name="sessions"),
                    Metric(name="bounceRate"),
                    Metric(name="averageSessionDuration")
                ],
                date_ranges=[DateRange(start_date=yesterday, end_date=yesterday)]
            )
            
            # Execute the request
            response = self.ga_service.run_report(request)
            
            # Process the response
            total_users = 0
            total_pageviews = 0
            total_sessions = 0
            page_views = {}
            traffic_sources = {}
            
            # Process each row
            for row in response.rows:
                page_path = row.dimension_values[0].value
                channel = row.dimension_values[1].value
                
                users = int(row.metric_values[0].value or 0)
                pageviews = int(row.metric_values[1].value or 0)
                sessions = int(row.metric_values[2].value or 0)
                
                total_users += users
                total_pageviews += pageviews
                total_sessions += sessions
                
                # Track page views
                page_views[page_path] = page_views.get(page_path, 0) + pageviews
                
                # Track traffic sources
                traffic_sources[channel] = traffic_sources.get(channel, 0) + sessions
            
            # Get bounce rate and session duration (these are property-level metrics)
            bounce_rate = 0
            avg_session_duration = 0
            
            if response.rows:
                # These metrics should be consistent across rows for the same time period
                bounce_rate = float(response.rows[0].metric_values[3].value or 0)
                avg_session_duration = float(response.rows[0].metric_values[4].value or 0)
            
            # Store the metrics
            self.metrics.website_visitors = total_users
            self.metrics.website_pageviews = total_pageviews  
            self.metrics.website_sessions = total_sessions
            self.metrics.bounce_rate = bounce_rate * 100  # Convert to percentage
            self.metrics.avg_session_duration = avg_session_duration
            
            # Sort and store top pages
            self.metrics.top_pages = [
                {'page': page, 'views': views}
                for page, views in sorted(page_views.items(), key=lambda x: x[1], reverse=True)[:5]
            ]
            
            # Sort and store traffic sources
            self.metrics.traffic_sources = dict(
                sorted(traffic_sources.items(), key=lambda x: x[1], reverse=True)[:5]
            )
            
            logging.info(f"✅ GA4 data collected: {total_users} users, {total_pageviews} pageviews, {total_sessions} sessions")
            
        except Exception as e:
            logging.error(f"❌ Error collecting GA4 data: {e}")
            logging.warning("⚠️  Falling back to mock data")
            self._generate_mock_ga_data()
    
    def collect_youtube_data(self):
        """Collect YouTube analytics data"""
        logging.info("📺 Collecting YouTube data...")
        
        if not self.youtube_service:
            logging.warning("⚠️  YouTube API not available - using mock data")
            self._generate_mock_youtube_data()
            return
        
        try:
            # Get channel ID from environment
            channel_id = os.getenv('YOUTUBE_CHANNEL_ID')
            if not channel_id:
                logging.error("❌ YOUTUBE_CHANNEL_ID not set in environment variables")
                self._generate_mock_youtube_data()
                return
            
            # Get channel statistics
            channels_response = self.youtube_service.channels().list(
                part='statistics',
                id=channel_id
            ).execute()
            
            if channels_response.get('items'):
                stats = channels_response['items'][0]['statistics']
                self.metrics.youtube_subscribers = int(stats.get('subscriberCount', 0))
                self.metrics.youtube_views = int(stats.get('viewCount', 0))
            
            # Get recent videos (last 24 hours)
            yesterday = (datetime.now() - timedelta(days=1)).isoformat() + 'Z'
            
            search_response = self.youtube_service.search().list(
                part='snippet',
                channelId=channel_id,
                publishedAfter=yesterday,
                type='video',
                maxResults=10
            ).execute()
            
            self.metrics.youtube_new_videos = len(search_response.get('items', []))
            
            # Get top performing videos (mock for now - would need YouTube Analytics API)
            self.metrics.top_videos = [
                {'title': item['snippet']['title'][:50] + '...', 'views': 'N/A'}
                for item in search_response.get('items', [])[:3]
            ]
            
            logging.info(f"✅ YouTube Data: {self.metrics.youtube_subscribers} subscribers, {self.metrics.youtube_new_videos} new videos")
            
        except Exception as e:
            logging.error(f"❌ Error collecting YouTube data: {e}")
            self._generate_mock_youtube_data()
    
    def _generate_mock_youtube_data(self):
        """Set YouTube fields to zero / empty when the API is unavailable."""
        logging.info("ℹ️  YouTube data unavailable — report will show zeroes for YouTube")
        self.metrics.youtube_subscribers = 0
        self.metrics.youtube_views = 0
        self.metrics.youtube_watch_time = 0.0
        self.metrics.youtube_new_videos = 0
        self.metrics.top_videos = []
    
    def collect_email_campaign_data(self):
        """Collect email campaign statistics from the AI engine."""
        logging.info("📧 Collecting email campaign data...")
        
        ai_engine_url = self.ai_engine_url
        try:
            if SCRAPING_AVAILABLE:
                r = requests.get(f"{ai_engine_url}/admin/api/overview", timeout=5)
                if r.status_code == 200:
                    data = r.json()
                    outreach = data.get("outreach", {})
                    sent = outreach.get("messages_sent", outreach.get("sent", 0))
                    delivered = outreach.get("delivered", 0)
                    self.metrics.emails_sent = sent + delivered
                    self.metrics.outreach_campaigns = outreach.get("total_logs", outreach.get("total", 0))
                    
                    # Calculate open rate from tracking data
                    total = self.metrics.outreach_campaigns
                    opened = outreach.get("emails_opened", outreach.get("opened", 0))
                    if total > 0:
                        self.metrics.email_open_rate = round((opened / total) * 100, 1)
                    
                    logging.info(f"✅ Email Data from AI engine: {self.metrics.emails_sent} sent, {self.metrics.outreach_campaigns} total")
                    return
        except Exception as e:
            logging.warning(f"⚠️  Could not reach AI engine for email data: {e}")
        
        # Fallback: check outreach system logs
        try:
            outreach_log_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'logs', 'music_outreach.log')
            if os.path.exists(outreach_log_file):
                with open(outreach_log_file, 'r') as f:
                    log_content = f.read()
                today = datetime.now().strftime('%Y-%m-%d')
                today_logs = [line for line in log_content.split('\n') if today in line]
                self.metrics.emails_sent = len([
                    line for line in today_logs 
                    if 'Email sent successfully' in line or '📧 Sent email to' in line
                ])
            logging.info(f"✅ Email Data (from logs): {self.metrics.emails_sent} emails sent")
        except Exception as e:
            logging.error(f"❌ Error collecting email data: {e}")
    
    def collect_outreach_data(self):
        """Collect outreach data from the AI engine API."""
        logging.info("🎯 Collecting outreach data...")
        
        ai_engine_url = self.ai_engine_url
        
        try:
            if not SCRAPING_AVAILABLE:
                raise ImportError("requests not available")
            
            # Get overview stats
            overview = requests.get(f"{ai_engine_url}/admin/api/overview", timeout=5).json()
            outreach_stats = overview.get("outreach", {})
            
            self.metrics.outreach_total_contacts = outreach_stats.get("playlists", 0) + outreach_stats.get("influencers", 0)
            self.metrics.outreach_emails_sent_today = outreach_stats.get("messages_sent", 0)
            self.metrics.outreach_status = {
                "sent": outreach_stats.get("messages_sent", 0),
                "delivered": outreach_stats.get("delivered", 0),
                "opened": outreach_stats.get("emails_opened", 0),
                "clicked": outreach_stats.get("links_clicked", 0),
                "logged": outreach_stats.get("logged", 0),
                "needs_dm": 0,
                "no_contact": 0,
            }
            
            # Get playlists and influencers as "sources"
            playlists = requests.get(f"{ai_engine_url}/outreach/playlists", timeout=5).json()
            influencers = requests.get(f"{ai_engine_url}/outreach/influencers", timeout=5).json()
            
            # Count recently discovered (last 24h)
            today = datetime.now().strftime('%Y-%m-%d')
            new_sources = []
            for p in playlists:
                new_sources.append({
                    'name': p.get('name', 'Unknown'),
                    'type': f"playlist ({p.get('platform', '?')})",
                    'contact': p.get('contact', 'none'),
                    'score': p.get('relevance_score', 0),
                })
            for i in influencers:
                new_sources.append({
                    'name': i.get('handle', 'Unknown'),
                    'type': f"influencer ({i.get('platform', '?')})",
                    'contact': i.get('contact', 'none'),
                    'score': i.get('relevance_score', 0),
                })
            self.metrics.outreach_new_sources = new_sources
            
            # Get outreach log for responses
            outreach_log = requests.get(f"{ai_engine_url}/admin/api/outreach?limit=50", timeout=5).json()
            responses = []
            for entry in outreach_log.get("items", []):
                if entry.get("status") in ("opened", "clicked", "delivered"):
                    responses.append({
                        'contact_name': f"{entry.get('target_type', '?')} #{entry.get('target_id', '?')}",
                        'type': entry.get('status', 'unknown'),
                        'response_date': entry.get('created_at', ''),
                        'summary': entry.get('subject', '')[:120],
                    })
            self.metrics.outreach_responses = responses
            
            # Get scheduler status
            try:
                sched = requests.get(f"{ai_engine_url}/scheduler/status", timeout=5).json()
                if sched.get("running"):
                    job_info = {j["id"]: j["next_run"] for j in sched.get("jobs", [])}
                    logging.info(f"✅ Scheduler running with {len(sched.get('jobs', []))} jobs")
            except Exception:
                pass
            
            logging.info(
                f"✅ Outreach Data: {self.metrics.outreach_total_contacts} total, "
                f"{len(new_sources)} sources, {len(responses)} responses/opens"
            )
            
        except Exception as e:
            logging.error(f"❌ Error collecting outreach data from AI engine: {e}")
            self._generate_mock_outreach_data()
    
    def _get_recent_new_sources(self):
        """Get list of recently discovered sources from outreach logs."""
        new_sources = []
        try:
            outreach_log = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'logs', 'music_outreach.log')
            if os.path.exists(outreach_log):
                today = datetime.now().strftime('%Y-%m-%d')
                with open(outreach_log, 'r') as f:
                    for line in f:
                        if today in line and 'New source discovered' in line:
                            new_sources.append({
                                'name': line.strip().split('discovered:')[-1].strip() if 'discovered:' in line else 'Unknown',
                                'type': 'discovered',
                                'discovered_date': today,
                            })
        except Exception:
            pass
        return new_sources
    
    def _get_recent_responses(self, responses_count):
        """Get list of recent responses from outreach logs."""
        responses = []
        try:
            outreach_log = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'logs', 'music_outreach.log')
            if os.path.exists(outreach_log) and responses_count > 0:
                with open(outreach_log, 'r') as f:
                    for line in f:
                        if 'Response received' in line or 'replied' in line.lower():
                            responses.append({
                                'contact_name': 'See outreach log',
                                'type': 'response',
                                'response_date': self.report_date,
                                'summary': line.strip()[-120:],
                            })
                            if len(responses) >= responses_count:
                                break
        except Exception:
            pass
        return responses
    
    def _generate_mock_outreach_data(self):
        """Set outreach fields to zero / empty when the outreach system is unavailable."""
        logging.info("ℹ️  Outreach data unavailable — report will show zeroes for outreach")
        self.metrics.outreach_total_contacts = 0
        self.metrics.outreach_emails_sent_today = 0
        self.metrics.outreach_status = {}
        self.metrics.outreach_new_sources = []
        self.metrics.outreach_responses = []
    
    def collect_voting_data(self):
        """Collect voting data from Google Sheets"""
        logging.info("🗳️  Collecting voting data...")
        
        # Google Sheets voting is not currently configured for NullRecords
        logging.info("ℹ️  Voting system not configured — skipping")
        self.metrics.new_votes = 0
        self.metrics.total_votes = 0
        self.metrics.voting_trends = {}
    
    def collect_voting_data_old(self):
        """Legacy Google Sheets voting data collection (disabled)"""
        try:
            from google_sheets_voting import GoogleSheetsVoting
            
            # Initialize Google Sheets voting system
            voting_system = GoogleSheetsVoting()
            voting_data = voting_system.get_voting_data()
            
            # Update metrics with real data
            self.metrics.new_votes = voting_data.new_votes_today
            self.metrics.total_votes = voting_data.total_votes
            self.metrics.voting_trends = voting_data.votes_by_artist
            
            logging.info(f"✅ Voting Data: {self.metrics.new_votes} new votes, {self.metrics.total_votes} total")
            
        except Exception as e:
            logging.error(f"❌ Error collecting Google Sheets voting data: {e}")
            
            # Fallback to empty data
            
            self.metrics.new_votes = 0
            self.metrics.total_votes = 0
            
            self.metrics.voting_trends = {}
            
            logging.info("ℹ️  Voting system not configured — skipping")
    
    def collect_news_monitoring_data(self):
        """Collect news and content monitoring statistics"""
        logging.info("📰 Collecting news monitoring data...")
        
        try:
            # Check news articles file — use workspace path
            workspace_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            news_file = os.path.join(workspace_root, 'docs', 'news_articles.json')
            if os.path.exists(news_file):
                with open(news_file, 'r') as f:
                    articles = json.load(f)
                
                # Count articles from today
                today = datetime.now().strftime('%Y-%m-%d')
                today_articles = [
                    article for article in articles 
                    if article.get('discovered_date', '').startswith(today)
                ]
                
                self.metrics.new_articles = len(today_articles)
                self.metrics.total_articles = len(articles)
                
                # Count releases
                releases = [
                    article for article in today_articles 
                    if article.get('article_type') == 'release'
                ]
                self.metrics.new_releases = len(releases)
                
                # Analyze sentiment — across all articles, not just today
                sentiment_counts = {}
                for article in articles:
                    sentiment = article.get('sentiment', 'neutral')
                    sentiment_counts[sentiment] = sentiment_counts.get(sentiment, 0) + 1
                
                self.metrics.content_sentiment = sentiment_counts
                
                # Count by source
                source_counts = {}
                for article in articles:
                    src = article.get('source', 'Unknown')
                    source_counts[src] = source_counts.get(src, 0) + 1
                
                # Build source HTML
                source_html_parts = []
                for src, count in sorted(source_counts.items(), key=lambda x: -x[1]):
                    source_html_parts.append(
                        f'<div class="metric-item"><span>{src}</span><span>{count}</span></div>'
                    )
                self.metrics.news_by_source_html = "\n".join(source_html_parts)
                
                # Verification summary from the articles themselves
                verified = sum(1 for a in articles if a.get('status') == 'verified')
                needs_verif = sum(1 for a in articles if a.get('status') == 'needs_verification')
                self.metrics.verification_data = {
                    "verified": verified,
                    "needs_verification": needs_verif,
                    "pending_articles": [
                        a for a in articles if a.get('status') == 'needs_verification'
                    ][:10]
                }
                
                # Count unique sources as monitoring sources
                self.metrics.monitoring_sources = len(source_counts)
            
            logging.info(f"✅ News Data: {self.metrics.new_articles} articles, {self.metrics.new_releases} releases")
            
        except Exception as e:
            logging.error(f"❌ Error collecting news data: {e}")
    
    def collect_system_health_data(self):
        """Collect system health and performance metrics, including AI engine status."""
        logging.info("🔧 Collecting system health data...")
        
        try:
            ai_engine_url = self.ai_engine_url
            website_url = os.getenv('WEBSITE_BASE_URL', 'https://nullrecords.com')
            
            # Check services
            api_tests = {
                website_url.replace('https://', '').replace('http://', ''): website_url,
                'ai-engine': f"{ai_engine_url}/admin/api/overview",
                'scheduler': f"{ai_engine_url}/scheduler/status",
            }
            
            if SCRAPING_AVAILABLE:
                for name, url in api_tests.items():
                    try:
                        start_time = time.time()
                        response = requests.get(url, timeout=5)
                        end_time = time.time()
                        
                        if response.status_code == 200:
                            self.metrics.api_response_times[name] = round((end_time - start_time) * 1000, 2)
                        else:
                            self.metrics.error_count += 1
                            self.metrics.api_response_times[name] = f'HTTP {response.status_code}'
                            
                    except Exception:
                        self.metrics.error_count += 1
                        self.metrics.api_response_times[name] = 'OFFLINE'
            
            # Check AI engine scheduler status
            try:
                sched_resp = requests.get(f"{ai_engine_url}/scheduler/status", timeout=5)
                if sched_resp.status_code == 200:
                    sched = sched_resp.json()
                    if sched.get("running"):
                        self.metrics.api_response_times['scheduler_jobs'] = len(sched.get("jobs", []))
                    else:
                        self.metrics.api_response_times['scheduler_jobs'] = 'STOPPED'
            except Exception:
                pass
            
            # Check log files for errors
            log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'logs')
            log_files = ['daily_report.log', 'music_outreach.log', 'news_monitor.log']
            today = datetime.now().strftime('%Y-%m-%d')
            
            for log_file in log_files:
                log_path = os.path.join(log_dir, log_file)
                if os.path.exists(log_path):
                    with open(log_path, 'r') as f:
                        content = f.read()
                        today_errors = len([
                            line for line in content.split('\n') 
                            if today in line and ('ERROR' in line or 'CRITICAL' in line)
                        ])
                        self.metrics.error_count += today_errors
            
            # Calculate uptime (simplified)
            if self.metrics.error_count == 0:
                self.metrics.system_uptime = 100.0
            else:
                self.metrics.system_uptime = max(95.0, 100.0 - (self.metrics.error_count * 2))
            
            logging.info(f"✅ System Health: {self.metrics.system_uptime}% uptime, {self.metrics.error_count} errors")
            
        except Exception as e:
            logging.error(f"❌ Error collecting system health data: {e}")
    
    def generate_report(self) -> str:
        """Generate comprehensive daily report"""
        logging.info("📋 Generating daily report...")
        
        # Collect all data
        self.collect_google_analytics_data()
        self.collect_youtube_data()
        self.collect_email_campaign_data()
        self.collect_outreach_data()
        self.collect_voting_data()
        self.collect_news_monitoring_data()
        self.collect_system_health_data()
        
        # Generate HTML report
        html_report = self._generate_html_report()
        
        # Save report to file
        report_filename = f"daily_report_{self.report_date}.html"
        with open(report_filename, 'w', encoding='utf-8') as f:
            f.write(html_report)
        
        logging.info(f"✅ Report generated: {report_filename}")
        return html_report
    
    def _generate_html_report(self) -> str:
        """Generate HTML formatted report"""
        # Calculate some derived metrics
        pages_per_session = round(self.metrics.website_pageviews / max(self.metrics.website_sessions, 1), 2)
        session_duration_minutes = round(self.metrics.avg_session_duration / 60, 1)
        
        # Generate traffic sources HTML
        traffic_sources_html = ""
        for source, visits in self.metrics.traffic_sources.items():
            percentage = round((visits / max(self.metrics.website_pageviews, 1)) * 100, 1)
            traffic_sources_html += f"""
                <div class="metric-item">
                    <span class="source-name">{source.title()}</span>
                    <span class="source-value">{visits} visits ({percentage}%)</span>
                </div>
            """
        
        # Generate top pages HTML
        top_pages_html = ""
        for page in self.metrics.top_pages:
            top_pages_html += f"""
                <div class="metric-item">
                    <span class="page-name">{page['page']}</span>
                    <span class="page-views">{page['views']} views</span>
                </div>
            """
        
        # Generate voting trends HTML
        voting_html = ""
        for category, votes in self.metrics.voting_trends.items():
            voting_html += f"""
                <div class="metric-item">
                    <span class="vote-category">{category}</span>
                    <span class="vote-count">{votes} votes</span>
                </div>
            """
        
        # Generate new sources HTML
        new_sources_html = ""
        if self.metrics.outreach_new_sources:
            for source in self.metrics.outreach_new_sources:
                score = source.get('score', 0)
                score_color = "#00ff88" if score >= 0.7 else "#ffff00" if score >= 0.4 else "#ff8888"
                contact = source.get('contact', 'none')
                contact_display = contact[:50] + '...' if len(contact) > 50 else contact
                new_sources_html += f"""
                    <div class="metric-item" style="display: block; margin-bottom: 10px; padding: 10px; background: rgba(0,255,255,0.1); border-radius: 5px;">
                        <div style="font-weight: bold; color: #00ffff;">{source['name']}</div>
                        <div style="font-size: 12px; color: #e0e0e0;">{source['type']} • Score: <span style="color:{score_color}">{score:.1f}</span></div>
                        <div style="font-size: 11px; color: #999;">Contact: {contact_display}</div>
                    </div>
                """
        else:
            new_sources_html = '<div style="text-align: center; color: #999; font-style: italic;">No new sources discovered today</div>'
        
        # Generate responses HTML  
        responses_html = ""
        if self.metrics.outreach_responses:
            for response in self.metrics.outreach_responses:
                status = response.get('type', 'unknown')
                response_color = "#00ff00" if status == "opened" else "#00ff88" if status == "delivered" else "#ffff00"
                responses_html += f"""
                    <div class="metric-item" style="display: block; margin-bottom: 10px; padding: 10px; background: rgba(0,255,255,0.1); border-radius: 5px;">
                        <div style="font-weight: bold; color: {response_color};">{response['contact_name']}</div>
                        <div style="font-size: 12px; color: #e0e0e0;">{status.title()}</div>
                        <div style="font-size: 11px; color: #ccc;">{response.get('summary', '')}</div>
                    </div>
                """
        else:
            responses_html = '<div style="text-align: center; color: #999; font-style: italic;">No responses received today</div>'
        
        html_content = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>NullRecords Daily Report - {self.report_date}</title>
            <style>
                body {{
                    font-family: 'JetBrains Mono', monospace;
                    background: linear-gradient(135deg, #1a1a2e, #16213e);
                    color: #ffffff;
                    margin: 0;
                    padding: 20px;
                    line-height: 1.6;
                }}
                .container {{
                    max-width: 1200px;
                    margin: 0 auto;
                    background: rgba(0,0,0,0.3);
                    border-radius: 10px;
                    padding: 30px;
                    border: 1px solid #00ffff;
                }}
                .header {{
                    text-align: center;
                    margin-bottom: 40px;
                    border-bottom: 2px solid #00ffff;
                    padding-bottom: 20px;
                }}
                .title {{
                    color: #00ffff;
                    font-size: 2.5em;
                    margin: 0;
                    text-shadow: 0 0 10px #00ffff;
                }}
                .subtitle {{
                    color: #ff0080;
                    font-size: 1.2em;
                    margin: 10px 0 0 0;
                }}
                .metrics-grid {{
                    display: grid;
                    grid-template-columns: repeat(auto-fit, minmax(350px, 1fr));
                    gap: 30px;
                    margin-bottom: 40px;
                }}
                .metric-card {{
                    background: rgba(255,255,255,0.05);
                    border: 1px solid #333;
                    border-radius: 10px;
                    padding: 25px;
                    transition: all 0.3s ease;
                }}
                .metric-card:hover {{
                    border-color: #00ffff;
                    box-shadow: 0 0 20px rgba(0,255,255,0.3);
                }}
                .card-title {{
                    color: #ff0080;
                    font-size: 1.3em;
                    margin-bottom: 20px;
                    border-bottom: 1px solid #333;
                    padding-bottom: 10px;
                }}
                .big-number {{
                    font-size: 3em;
                    color: #00ffff;
                    font-weight: bold;
                    text-align: center;
                    margin: 20px 0;
                    text-shadow: 0 0 10px #00ffff;
                }}
                .metric-item {{
                    display: flex;
                    justify-content: space-between;
                    padding: 8px 0;
                    border-bottom: 1px solid rgba(255,255,255,0.1);
                }}
                .metric-item:last-child {{
                    border-bottom: none;
                }}
                .status-good {{ color: #00ff00; }}
                .status-warning {{ color: #ffff00; }}
                .status-error {{ color: #ff0000; }}
                .summary-section {{
                    background: rgba(0,255,255,0.1);
                    border: 1px solid #00ffff;
                    border-radius: 10px;
                    padding: 25px;
                    margin-top: 30px;
                }}
                .summary-title {{
                    color: #00ffff;
                    font-size: 1.5em;
                    margin-bottom: 15px;
                }}
                .highlight {{
                    color: #ff0080;
                    font-weight: bold;
                }}
                .card-title a {{
                    color: inherit;
                    text-decoration: none;
                    border-bottom: 1px dashed rgba(255,0,128,0.4);
                    transition: all 0.2s;
                }}
                .card-title a:hover {{
                    color: #00ffff;
                    border-bottom-color: #00ffff;
                }}
                .drill-link {{
                    display: inline-block;
                    margin-top: 12px;
                    padding: 6px 14px;
                    background: rgba(0,255,255,0.15);
                    border: 1px solid #00ffff;
                    border-radius: 5px;
                    color: #00ffff;
                    text-decoration: none;
                    font-size: 12px;
                    transition: all 0.2s;
                }}
                .drill-link:hover {{
                    background: rgba(0,255,255,0.3);
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1 class="title">🎵 NULLRECORDS DAILY REPORT</h1>
                    <p class="subtitle">System Status & Analytics - {self.report_date}</p>
                </div>
                
                <div class="metrics-grid">
                    <!-- Website Analytics -->
                    <div class="metric-card">
                        <h3 class="card-title"><a href="http://localhost:4000/" title="Open site">🌐 Website Analytics</a></h3>
                        <div class="big-number">{self.metrics.website_visitors}</div>
                        <div style="text-align: center; color: #e0e0e0; margin-bottom: 20px;">Unique Visitors</div>
                        
                        <div class="metric-item">
                            <span>Page Views:</span>
                            <span class="highlight">{self.metrics.website_pageviews}</span>
                        </div>
                        <div class="metric-item">
                            <span>Sessions:</span>
                            <span>{self.metrics.website_sessions}</span>
                        </div>
                        <div class="metric-item">
                            <span>Pages/Session:</span>
                            <span>{pages_per_session}</span>
                        </div>
                        <div class="metric-item">
                            <span>Avg Session:</span>
                            <span>{session_duration_minutes} min</span>
                        </div>
                        <div class="metric-item">
                            <span>Bounce Rate:</span>
                            <span>{self.metrics.bounce_rate:.1f}%</span>
                        </div>
                        
                        <h4 style="color: #00ffff; margin: 20px 0 10px 0;">Top Pages:</h4>
                        {top_pages_html}
                    </div>
                    
                    <!-- Traffic Sources -->
                    <div class="metric-card">
                        <h3 class="card-title"><a href="https://analytics.google.com" target="_blank" title="Google Analytics">🚀 Traffic Sources</a></h3>
                        {traffic_sources_html}
                    </div>
                    
                    <!-- YouTube Metrics -->
                    <div class="metric-card">
                        <h3 class="card-title"><a href="https://studio.youtube.com" target="_blank" title="YouTube Studio">📺 YouTube Channel</a></h3>
                        <div class="big-number">{self.metrics.youtube_subscribers}</div>
                        <div style="text-align: center; color: #e0e0e0; margin-bottom: 20px;">Subscribers</div>
                        
                        <div class="metric-item">
                            <span>Total Views:</span>
                            <span class="highlight">{self.metrics.youtube_views:,}</span>
                        </div>
                        <div class="metric-item">
                            <span>New Videos (24h):</span>
                            <span>{self.metrics.youtube_new_videos}</span>
                        </div>
                        <div class="metric-item">
                            <span>Watch Time:</span>
                            <span>{self.metrics.youtube_watch_time:.0f} hours</span>
                        </div>
                    </div>
                    
                    <!-- Email Campaigns -->
                    <div class="metric-card">
                        <h3 class="card-title"><a href="https://app.brevo.com" target="_blank" title="Brevo Dashboard">📧 Email Campaigns</a></h3>
                        <div class="big-number">{self.metrics.emails_sent}</div>
                        <div style="text-align: center; color: #e0e0e0; margin-bottom: 20px;">Emails Sent Today</div>
                        
                        <div class="metric-item">
                            <span>Outreach Campaigns:</span>
                            <span class="highlight">{self.metrics.outreach_campaigns}</span>
                        </div>
                        <div class="metric-item">
                            <span>Open Rate:</span>
                            <span>{self.metrics.email_open_rate:.1f}%</span>
                        </div>
                        <div class="metric-item">
                            <span>Click Rate:</span>
                            <span>{self.metrics.email_click_rate:.1f}%</span>
                        </div>
                    </div>
                    
                    <!-- Outreach & Discovery -->
                    <div class="metric-card">
                        <h3 class="card-title"><a href="http://localhost:8008/admin/contacts" title="Contacts Report">🎯 Music Outreach</a></h3>
                        <div class="big-number">{self.metrics.outreach_emails_sent_today}</div>
                        <div style="text-align: center; color: #e0e0e0; margin-bottom: 10px;">Emails Sent Today</div>
                        <div style="text-align: center; color: #00ffff; margin-bottom: 20px; font-size: 12px;">
                            Targeting: LoFi • Nu Jazz • Jazz Fusion • Indie Artists
                        </div>
                        
                        <div class="metric-item">
                            <span>Total Contacts:</span>
                            <span class="highlight">{self.metrics.outreach_total_contacts}</span>
                        </div>
                        <div class="metric-item">
                            <span>Sent:</span>
                            <span style="color: #00ff88">{self.metrics.outreach_status.get('sent', 0)}</span>
                        </div>
                        <div class="metric-item">
                            <span>Delivered:</span>
                            <span style="color: #00ff88">{self.metrics.outreach_status.get('delivered', 0)}</span>
                        </div>
                        <div class="metric-item">
                            <span>Opened:</span>
                            <span style="color: #00ffff">{self.metrics.outreach_status.get('opened', 0)}</span>
                        </div>
                        <div class="metric-item">
                            <span>Logged (no email):</span>
                            <span>{self.metrics.outreach_status.get('logged', 0)}</span>
                        </div>
                        <div class="metric-item">
                            <span>Needs DM:</span>
                            <span>{self.metrics.outreach_status.get('needs_dm', 0)}</span>
                        </div>
                    </div>
                    
                    <!-- New Sources Discovered -->
                    <div class="metric-card">
                        <h3 class="card-title"><a href="http://localhost:8008/admin/contacts" title="Contacts Report — All Sources">🔍 New Sources Discovered</a></h3>
                        <div class="big-number">{len(self.metrics.outreach_new_sources or [])}</div>
                        <div style="text-align: center; color: #e0e0e0; margin-bottom: 20px;">Sources Added Today</div>
                        
                        {new_sources_html}
                    </div>
                    
                    <!-- Outreach Responses -->
                    <div class="metric-card">
                        <h3 class="card-title"><a href="http://localhost:8008/admin/contacts?outreach_status=opened" title="Contacts Report — Opened">📬 Responses Received</a></h3>
                        <div class="big-number">{len(self.metrics.outreach_responses or [])}</div>
                        <div style="text-align: center; color: #e0e0e0; margin-bottom: 20px;">Responses Today</div>
                        
                        {responses_html}
                    </div>
                    
                    <!-- Voting & Engagement -->
                    <div class="metric-card">
                        <h3 class="card-title"><a href="http://localhost:4000/store/" title="Store & Voting">🗳️ Voting & Engagement</a></h3>
                        <div class="big-number">{self.metrics.new_votes}</div>
                        <div style="text-align: center; color: #e0e0e0; margin-bottom: 20px;">New Votes Today</div>
                        
                        <div class="metric-item">
                            <span>Total Votes:</span>
                            <span class="highlight">{self.metrics.total_votes}</span>
                        </div>
                        
                        <h4 style="color: #00ffff; margin: 20px 0 10px 0;">Voting Trends:</h4>
                        {voting_html}
                    </div>
                    
                    <!-- News & Content -->
                    <div class="metric-card">
                        <h3 class="card-title"><a href="http://localhost:8008/admin/news" title="News Management">📰 News Monitoring</a></h3>
                        <div class="big-number">{self.metrics.new_articles}</div>
                        <div style="text-align: center; color: #e0e0e0; margin-bottom: 20px;">New Articles Today</div>
                        
                        <div class="metric-item">
                            <span>Total Articles:</span>
                            <span class="highlight">{getattr(self.metrics, 'total_articles', 0)}</span>
                        </div>
                        <div class="metric-item">
                            <span>New Releases:</span>
                            <span class="highlight">{self.metrics.new_releases}</span>
                        </div>
                        <div class="metric-item">
                            <span>Monitoring Sources:</span>
                            <span>{self.metrics.monitoring_sources}</span>
                        </div>
                        <div class="metric-item">
                            <span>Positive Sentiment:</span>
                            <span class="status-good">{self.metrics.content_sentiment.get('positive', 0)}</span>
                        </div>
                        <div class="metric-item">
                            <span>Verified Articles:</span>
                            <span class="status-good">{getattr(self.metrics, 'verification_data', {}).get('verified', 0)}</span>
                        </div>
                        <div class="metric-item">
                            <span>Needs Verification:</span>
                            <span class="{'status-warning' if getattr(self.metrics, 'verification_data', {}).get('needs_verification', 0) > 0 else 'status-good'}">{getattr(self.metrics, 'verification_data', {}).get('needs_verification', 0)}</span>
                        </div>
                        
                        <h4 style="color: #00ffff; margin: 20px 0 10px 0;">By Source:</h4>
                        {getattr(self.metrics, 'news_by_source_html', '<div style="color:#999">No source data</div>')}
                        
                        <a href="http://localhost:8008/admin/news" class="drill-link">📋 Manage Articles</a>
                        <a href="http://localhost:4000/news/" class="drill-link" style="margin-left: 8px;">🌐 Public News</a>
                    </div>
                    
                    <!-- System Health -->
                    <div class="metric-card">
                        <h3 class="card-title"><a href="http://localhost:8008/scheduler/status" title="Scheduler Status">🔧 System Health</a></h3>
                        <div class="big-number status-good">{self.metrics.system_uptime:.1f}%</div>
                        <div style="text-align: center; color: #e0e0e0; margin-bottom: 20px;">Uptime</div>
                        
                        <div class="metric-item">
                            <span>Errors Today:</span>
                            <span class="{'status-good' if self.metrics.error_count == 0 else 'status-warning'}">{self.metrics.error_count}</span>
                        </div>
                        
                        <h4 style="color: #00ffff; margin: 20px 0 10px 0;">Response Times:</h4>
                        {"".join([f'<div class="metric-item"><span>{name}:</span><span>{time}ms</span></div>' for name, time in self.metrics.api_response_times.items()])}
                    </div>
                </div>
                
                <!-- Verification Requests -->
                {self._generate_verification_section()}
                
                <!-- Quick Navigation -->
                <div style="text-align:center; margin: 20px 0; padding: 12px; background: rgba(0,255,255,0.05); border: 1px solid rgba(0,255,255,0.2); border-radius: 8px;">
                    <span style="color: #888; font-size: 12px; text-transform: uppercase; letter-spacing: 1px;">Quick Links: </span>
                    <a href="http://localhost:8008/admin/contacts" style="color:#00ffff; text-decoration:none; margin: 0 8px; font-size: 13px;">📋 Contacts Report</a> |
                    <a href="http://localhost:8008/admin" style="color:#00ffff; text-decoration:none; margin: 0 8px; font-size: 13px;">⚙️ AI Engine</a> |
                    <a href="http://localhost:8008/admin/news" style="color:#00ffff; text-decoration:none; margin: 0 8px; font-size: 13px;">📰 News</a> |
                    <a href="http://localhost:4000/ops/presave-content-calendar.html" style="color:#00ffff; text-decoration:none; margin: 0 8px; font-size: 13px;">📅 Calendar</a> |
                    <a href="http://localhost:8008/admin/contacts/export?format=csv" style="color:#facc15; text-decoration:none; margin: 0 8px; font-size: 13px;">📥 Export Contacts CSV</a>
                </div>

                <!-- Executive Summary -->
                <div class="summary-section">
                    <h3 class="summary-title">📊 Executive Summary</h3>
                    <p>
                        <span class="highlight">Website Traffic:</span> {self.metrics.website_visitors} unique visitors generated {self.metrics.website_pageviews} page views 
                        across {self.metrics.website_sessions} sessions, with an average session duration of {session_duration_minutes} minutes.
                    </p>
                    <p>
                        <span class="highlight">Content & Outreach:</span> {self.metrics.outreach_total_contacts} total outreach contacts. 
                        {self.metrics.outreach_status.get('sent', 0) + self.metrics.outreach_status.get('delivered', 0)} emails sent, 
                        {self.metrics.outreach_status.get('opened', 0)} opened.
                        Discovered {self.metrics.new_articles} new articles and {self.metrics.new_releases} new releases. 
                        Received {self.metrics.new_votes} new votes bringing total to {self.metrics.total_votes}.
                    </p>
                    <p>
                        <span class="highlight">System Performance:</span> Maintaining {self.metrics.system_uptime:.1f}% uptime with {self.metrics.error_count} errors. 
                        YouTube channel has {self.metrics.youtube_subscribers} subscribers with {self.metrics.youtube_new_videos} new videos published.
                    </p>
                    <p>
                        <span class="highlight">Top Traffic Source:</span> {list(self.metrics.traffic_sources.keys())[0] if self.metrics.traffic_sources else 'N/A'} 
                        ({list(self.metrics.traffic_sources.values())[0] if self.metrics.traffic_sources else 0} visits)
                    </p>
                </div>
                
                <div style="text-align: center; margin-top: 30px; color: #cccccc; font-size: 0.9em;">
                    Report generated at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} • 
                    <a href="{os.getenv('WEBSITE_BASE_URL', 'https://nullrecords.com')}" style="color: #00ffff;">{os.getenv('WEBSITE_BASE_URL', 'https://nullrecords.com').replace('https://', '').replace('http://', '')}</a>
                </div>
            </div>
        </body>
        </html>
        """
        
        return html_content

    def _generate_verification_section(self) -> str:
        """Generate verification requests section for email"""
        verification_data = getattr(self.metrics, 'verification_data', {})
        pending_articles = verification_data.get('pending_articles', [])
        
        if not pending_articles:
            return ""
        
        articles_html = ""
        for article in pending_articles:
            artist = article.get('artist_mentioned', article.get('artist', ''))
            if isinstance(artist, list):
                artist = ', '.join(artist)
            article_type = article.get('article_type', article.get('type', 'unknown'))
            excerpt = article.get('excerpt', article.get('content', ''))[:200]
            url = article.get('url', '')
            articles_html += f"""
                <div class="verification-item">
                    <h4 style="color: #00ffff; margin: 0 0 5px 0;">{article.get('title', 'Untitled')}</h4>
                    <p style="margin: 5px 0; color: #e0e0e0; font-size: 0.9em;">
                        <strong>Source:</strong> {article.get('source', 'Unknown')} | 
                        <strong>Type:</strong> {article_type} | 
                        <strong>Artist:</strong> {artist}
                    </p>
                    <p style="margin: 5px 0 10px 0; color: #eee; font-size: 0.9em;">{excerpt}</p>
                    {'<p style="margin: 0 0 15px 0;"><a href="' + url + '" style="color: #ff5758; text-decoration: none;">View Article</a></p>' if url else ''}
                </div>
            """
        
        return f"""
                <div class="verification-section" style="background: #2a2a2a; border: 2px solid #ff5758; border-radius: 8px; padding: 20px; margin: 20px 0;">
                    <h3 style="color: #ff5758; margin: 0 0 15px 0;">❓ Content Verification Needed</h3>
                    <p style="color: #e0e0e0; margin: 0 0 15px 0;">
                        The following {len(pending_articles)} articles were found but need verification to confirm they're actually about NullRecords:
                    </p>
                    {articles_html}
                    <p style="color: #00ffff; font-size: 0.9em; margin: 15px 0 0 0;">
                        <strong>Action Required:</strong> Please review these articles and reply to this email with:
                        <br>• ✅ "VERIFY: [article title]" to approve
                        <br>• ❌ "REJECT: [article title]" to remove
                    </p>
                </div>
        """
    
    def send_daily_email(self, html_report: str):
        """Send daily report via email"""
        logging.info("📧 Sending daily report email...")
        
        try:
            # Get SMTP credentials from environment (same as outreach system)
            smtp_server = os.getenv('SMTP_SERVER')
            smtp_port = int(os.getenv('SMTP_PORT', '587'))
            smtp_username = os.getenv('SMTP_USER')  # Use SMTP_USER like outreach system
            smtp_password = os.getenv('SMTP_PASSWORD')
            sender_email = os.getenv('SENDER_EMAIL')
            recipient_email = os.getenv('DAILY_REPORT_EMAIL') or os.getenv('BCC_EMAIL')
            cc_email = os.getenv('CC_EMAIL')
            
            if not smtp_username or not smtp_password or not smtp_server or not sender_email or not recipient_email:
                logging.error("❌ SMTP credentials not configured - missing required environment variables")
                logging.error("Required: SMTP_SERVER, SMTP_USER, SMTP_PASSWORD, SENDER_EMAIL, DAILY_REPORT_EMAIL (or BCC_EMAIL)")
                return False
            
            # Check opt-out status
            if OPT_OUT_AVAILABLE and check_opt_out(recipient_email, "daily_reports"):
                logging.info(f"⚠️  Recipient {recipient_email} has opted out of daily reports - skipping email")
                return True  # Return True since this is expected behavior
            
            # Create email
            subject = f"🎵 NullRecords Daily Report - {self.report_date}"
            
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = sender_email
            msg['To'] = recipient_email
            if cc_email:
                msg['Cc'] = cc_email
            
            # Add opt-out link to HTML report if available
            if OPT_OUT_AVAILABLE:
                opt_out_link = get_opt_out_link(recipient_email)
                html_report = html_report.replace(
                    '</body>',
                    f'''
                    <div style="text-align: center; margin-top: 40px; padding: 20px; background-color: rgba(0,255,255,0.1); border: 1px solid #00ffff; border-radius: 10px; font-size: 12px; color: #e0e0e0;">
                        <p style="margin: 5px 0; color: #ffffff;">You're receiving this because you requested daily reports from NullRecords.</p>
                        <p style="margin: 5px 0;"><a href="{opt_out_link}" style="color: #00ffff; text-decoration: underline;">Unsubscribe from these emails</a></p>
                    </div>
                    </body>'''
                )
            
            # Add HTML content
            html_part = MIMEText(html_report, 'html')
            msg.attach(html_part)
            
            # Send email
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(smtp_username, smtp_password)
                
                # Send to all recipients (To + CC)
                all_recipients = [recipient_email]
                if cc_email:
                    all_recipients.append(cc_email)
                
                server.send_message(msg, to_addrs=all_recipients)
            
            recipient_list = recipient_email
            if cc_email:
                recipient_list += f" (CC: {cc_email})"
            logging.info(f"✅ Daily report sent to {recipient_list}")
            return True
            
        except Exception as e:
            logging.error(f"❌ Failed to send daily report: {e}")
            return False

def main():
    """Main execution function"""
    parser = argparse.ArgumentParser(description='NullRecords Daily Status Report System')
    parser.add_argument('--send-email', action='store_true', help='Send report via email')
    parser.add_argument('--date', type=str, help='Report date (YYYY-MM-DD), defaults to today')
    parser.add_argument('--output', type=str, help='Output file path for HTML report')
    
    args = parser.parse_args()
    
    # Initialize report system
    report_system = DailyReportSystem()
    
    if args.date:
        report_system.report_date = args.date
        report_system.metrics.date = args.date
    
    # Generate report
    html_report = report_system.generate_report()
    
    # Save to custom output path if specified
    if args.output:
        # Ensure directory exists
        output_dir = os.path.dirname(args.output)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(html_report)
        print(f"✅ Report saved to {args.output}")
    else:
        # Save to default reports directory with timestamp
        reports_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'daily_reports')
        os.makedirs(reports_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'daily_report_{timestamp}.html'
        output_path = os.path.join(reports_dir, filename)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_report)
        
        # Also create a "latest" copy for easy access
        latest_path = os.path.join(reports_dir, 'daily_report_latest.html')
        dashboard_latest = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'daily_report_latest.html')
        try:
            import shutil
            shutil.copy2(output_path, latest_path)
            shutil.copy2(output_path, dashboard_latest)
        except Exception:
            pass
            
        print(f"✅ Report saved to {output_path}")
    
    # Send email if requested
    if args.send_email:
        success = report_system.send_daily_email(html_report)
        if success:
            print("✅ Daily report email sent successfully")
        else:
            print("❌ Failed to send daily report email")
    
    print(f"📊 Daily report generated for {report_system.report_date}")
    print(f"📈 Key metrics: {report_system.metrics.website_visitors} visitors, {report_system.metrics.emails_sent} emails sent")

if __name__ == "__main__":
    main()
