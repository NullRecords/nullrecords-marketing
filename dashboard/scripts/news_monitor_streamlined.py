#!/usr/bin/env python3
"""
NullRecords News Monitoring System - Streamlined Version
======================================================

Automated news collection focused on reliable sources for playlist discovery
and music industry mentions of NullRecords artists.

Usage: python news_monitor_streamlined.py [--collect] [--test]
"""

import json
import time
import random
import re
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set
import logging
from dataclasses import dataclass, asdict, field
from pathlib import Path
import argparse
from urllib.parse import urljoin, urlparse, quote
import hashlib

SCRIPT_DIR = Path(__file__).resolve().parent
DASHBOARD_DIR = SCRIPT_DIR.parent
WORKSPACE_DIR = DASHBOARD_DIR.parent
LOG_DIR = DASHBOARD_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Optional dependencies
try:
    import requests
    from bs4 import BeautifulSoup
    SCRAPING_AVAILABLE = True
except ImportError:
    SCRAPING_AVAILABLE = False
    logging.warning("⚠️  Scraping libraries not available - install requests and beautifulsoup4")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / 'news_monitor.log'),
        logging.StreamHandler()
    ]
)

@dataclass
class NewsArticle:
    """News article data structure"""
    id: str
    title: str
    content: str
    source: str
    url: str
    author: Optional[str] = None
    published_date: Optional[str] = None
    discovered_date: str = field(default_factory=lambda: datetime.now().isoformat())
    artist_mentioned: List[str] = field(default_factory=list)
    sentiment: str = "neutral"  # positive, negative, neutral
    article_type: str = "review"  # review, news, interview, feature
    status: str = "new"  # new, processed, published, needs_verification, verified
    tags: List[str] = field(default_factory=list)
    excerpt: Optional[str] = None
    
    def __post_init__(self):
        if not self.id:
            self.id = hashlib.md5(f"{self.title}{self.url}{self.source}".encode()).hexdigest()[:12]
        if not self.excerpt and self.content:
            self.excerpt = self.content[:200] + "..." if len(self.content) > 200 else self.content

class StreamlinedNewsMonitor:
    """Streamlined news monitoring focused on reliable sources"""
    
    def __init__(self):
        self.articles: List[NewsArticle] = []
        self.data_file = WORKSPACE_DIR / "docs" / "news_articles.json"
        self.artists = [
            "NullRecords",
            "My Evil Robot Army", 
            "MERA",
            "Null Records",
            "My Evil Robot Army - Space Jazz",
            "Evil Robot Army"
        ]
        self.search_sources = self._initialize_reliable_sources()
        self.failed_sources = set()  
        self.load_articles()
        
    def _initialize_reliable_sources(self) -> List[Dict]:
        """Initialize only reliable, tested news sources"""
        return [
            # Proven reliable sources
            {
                "name": "Google Spotify Playlists",
                "search_url": "https://www.google.com/search?q=site:open.spotify.com+\"{query}\"+playlist",
                "type": "playlist_search",
                "selector": ".g"
            },
            {
                "name": "Google Apple Music Playlists",
                "search_url": "https://www.google.com/search?q=site:music.apple.com+\"{query}\"+playlist",
                "type": "playlist_search",
                "selector": ".g"
            },
            {
                "name": "Google YouTube Playlists",
                "search_url": "https://www.google.com/search?q=site:youtube.com+\"{query}\"+playlist",
                "type": "playlist_search",
                "selector": ".g"
            },
            {
                "name": "Bandcamp Search",
                "search_url": "https://bandcamp.com/search?q={query}",
                "type": "streaming",
                "selector": ".searchresult"
            },
            {
                "name": "SoundCloud Search",
                "search_url": "https://soundcloud.com/search?q={query}",
                "type": "streaming",
                "selector": ".soundList__item"
            },
            {
                "name": "Reddit LoFi Community",
                "search_url": "https://www.reddit.com/r/LofiHipHop/search/?q={query}",
                "type": "social",
                "selector": ".Post"
            },
            {
                "name": "DuckDuckGo Playlist Search",
                "search_url": "https://duckduckgo.com/?q=\"{query}\"+spotify+playlist",
                "type": "search",
                "selector": ".result"
            }
        ]
    
    def load_articles(self):
        """Load existing articles from JSON file"""
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.articles = []
                    for article in data:
                        article.setdefault("content", article.get("excerpt", ""))
                        article.setdefault("source", "unknown")
                        article.setdefault("url", "")
                        self.articles.append(NewsArticle(**article))
                logging.info(f"📰 Loaded {len(self.articles)} existing articles")
            except Exception as e:
                logging.error(f"❌ Error loading articles: {e}")
                self.articles = []
        else:
            self.articles = []
    
    def save_articles(self):
        """Save articles to JSON file"""
        try:
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump([asdict(article) for article in self.articles], f, indent=2, ensure_ascii=False)
            logging.info(f"💾 Saved {len(self.articles)} articles")
        except Exception as e:
            logging.error(f"❌ Error saving articles: {e}")
    
    def search_for_mentions(self, artist: str, limit: int = 3) -> List[NewsArticle]:
        """Search reliable sources for mentions of artist"""
        if not SCRAPING_AVAILABLE:
            logging.warning("⚠️  Scraping not available - install requests and beautifulsoup4")
            return []
        
        found_articles = []
        
        for source in self.search_sources:
            # Skip sources that have failed consistently
            if source['name'] in self.failed_sources:
                logging.debug(f"⏭️  Skipping {source['name']} (previously failed)")
                continue
                
            try:
                logging.info(f"🔍 Searching {source['name']} for '{artist}'...")
                
                # Format search query
                query = quote(artist)
                search_url = source['search_url'].format(query=query)
                
                # Conservative headers to avoid blocking
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Encoding': 'identity',  # Avoid compression issues
                }
                
                # Make request with conservative timeout
                response = requests.get(search_url, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    # Parse with BeautifulSoup
                    soup = BeautifulSoup(response.content, 'html.parser')
                    
                    # Generic article extraction - simplified
                    articles = soup.find_all(['article', 'div', 'li'], limit=limit*2)
                    
                    for article_elem in articles[:limit]:
                        try:
                            # Extract basic info
                            title_elem = article_elem.find(['h1', 'h2', 'h3', 'h4', 'a'])
                            title = title_elem.get_text(strip=True) if title_elem else "No title found"
                            
                            # Check if content is relevant
                            content_text = article_elem.get_text(strip=True)
                            confidence_score = self._calculate_relevance_confidence(title, content_text, artist)
                            
                            if confidence_score > 0.4:  # Conservative threshold
                                # Try to extract URL
                                link_elem = article_elem.find('a', href=True)
                                article_url = link_elem['href'] if link_elem else search_url
                                
                                # Make URL absolute if relative
                                if article_url.startswith('/'):
                                    base_url = f"{urlparse(search_url).scheme}://{urlparse(search_url).netloc}"
                                    article_url = urljoin(base_url, article_url)
                                
                                # Determine status and type
                                status = "verified" if confidence_score > 0.8 else "needs_verification"
                                article_type = self._determine_article_type(title, content_text)
                                
                                article = NewsArticle(
                                    id="",  # Will be generated
                                    title=title,
                                    content=content_text[:500],
                                    source=source['name'],
                                    url=article_url,
                                    artist_mentioned=[artist],
                                    article_type=article_type,
                                    status=status
                                )
                                found_articles.append(article)
                                
                                confidence_emoji = "✅" if confidence_score > 0.8 else "❓" if confidence_score > 0.6 else "⚠️"
                                logging.info(f"{confidence_emoji} Found ({confidence_score:.2f}): {title[:50]}...")
                        
                        except Exception as e:
                            logging.debug(f"Error parsing article: {e}")
                            continue
                
                # Rate limiting between requests
                time.sleep(random.uniform(1, 3))
                
            except Exception as e:
                logging.error(f"❌ Error searching {source['name']}: {e}")
                self.failed_sources.add(source['name'])
                continue
        
        return found_articles

    def _calculate_relevance_confidence(self, title: str, content: str, artist: str) -> float:
        """Calculate confidence that content is actually about our artist"""
        confidence = 0.0
        
        # Combine title and content for analysis
        text = f"{title} {content}".lower()
        artist_lower = artist.lower()
        
        # Base score for artist name mention
        if artist_lower in text:
            confidence += 0.6
        
        # Higher score if artist name is in title
        if artist_lower in title.lower():
            confidence += 0.3
        
        # Playlist-specific keywords that indicate real playlist placements
        playlist_keywords = [
            'playlist', 'featured in', 'added to', 'curated', 'included in',
            'now playing', 'discover', 'spotify playlist', 'lofi playlist',
            'chill playlist', 'study playlist', 'ambient playlist', 'mix'
        ]
        
        # Music-related keywords
        music_keywords = [
            'music', 'album', 'track', 'song', 'release', 'artist', 'musician',
            'band', 'review', 'listen', 'stream', 'electronic', 'lofi', 'jazz'
        ]
        
        playlist_score = sum(1 for keyword in playlist_keywords if keyword in text) / len(playlist_keywords)
        confidence += playlist_score * 0.4
        
        music_score = sum(1 for keyword in music_keywords if keyword in text) / len(music_keywords)
        confidence += music_score * 0.2
        
        # Penalty for spam/irrelevant content
        spam_indicators = [
            'lorem ipsum', 'example.com', 'test article', 'placeholder',
            'buy now', 'click here', 'advertisement', 'fake'
        ]
        
        if any(indicator in text for indicator in spam_indicators):
            confidence *= 0.1
        
        return min(confidence, 1.0)
    
    def _determine_article_type(self, title: str, content: str) -> str:
        """Determine the type of article based on content"""
        text = f"{title} {content}".lower()
        
        if any(word in text for word in ["playlist", "featured in", "added to"]):
            return "playlist"
        elif any(word in text for word in ["review", "rating", "critique"]):
            return "review"
        elif any(word in text for word in ["interview", "talks with", "speaks to"]):
            return "interview"
        else:
            return "news"
    
    def collect_news(self, max_per_artist: int = 2):
        """Collect news for all artists using reliable sources"""
        logging.info("🔍 Starting streamlined news collection...")
        
        new_articles = []
        
        for artist in self.artists:
            logging.info(f"📰 Collecting news for: {artist}")
            articles = self.search_for_mentions(artist, limit=max_per_artist)
            
            for article in articles:
                # Check if we already have this article
                existing = next((a for a in self.articles if a.title == article.title and a.source == article.source), None)
                if not existing:
                    new_articles.append(article)
                    logging.info(f"✅ New article: {article.title[:50]}...")
        
        self.articles.extend(new_articles)
        self.save_articles()
        
        logging.info(f"🎉 Collected {len(new_articles)} new articles from reliable sources")
        
        if self.failed_sources:
            logging.info(f"⚠️  Failed sources: {', '.join(self.failed_sources)}")
        
        return len(new_articles)
    
    def test_sources(self):
        """Test all sources to see which ones are working"""
        logging.info("🧪 Testing all sources...")
        
        for source in self.search_sources:
            try:
                query = quote("test")
                search_url = source['search_url'].format(query=query)
                
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
                }
                
                response = requests.get(search_url, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    logging.info(f"✅ {source['name']} - Working")
                else:
                    logging.warning(f"⚠️  {source['name']} - Status {response.status_code}")
                    
            except Exception as e:
                logging.error(f"❌ {source['name']} - Failed: {e}")
                
            time.sleep(1)  # Rate limiting

def main():
    parser = argparse.ArgumentParser(description='Streamlined NullRecords News Monitoring')
    parser.add_argument('--collect', action='store_true', help='Collect new articles')
    parser.add_argument('--test', action='store_true', help='Test all sources')
    parser.add_argument('--limit', type=int, default=2, help='Limit articles per artist')
    
    args = parser.parse_args()
    
    monitor = StreamlinedNewsMonitor()
    
    if args.test:
        monitor.test_sources()
    elif args.collect:
        monitor.collect_news(max_per_artist=args.limit)
    else:
        print("🎵 Streamlined NullRecords News Monitoring")
        print("=========================================")
        print("Available commands:")
        print("  --collect   Collect new articles from reliable sources")
        print("  --test      Test all sources")
        print("  --limit N   Limit to N articles per artist")
        print("")
        print("Example: python news_monitor_streamlined.py --collect --limit 2")

if __name__ == "__main__":
    main()
