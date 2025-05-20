from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import instaloader
from slowapi import Limiter
from slowapi.util import get_remote_address
from cachetools import TTLCache
import logging
import time

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Instagram Profile Fetcher", 
              description="API to fetch Instagram profile details for verification service")

# Rate limiting setup
limiter = Limiter(key_func=get_remote_address)

# Cache setup (1 hour TTL)
profile_cache = TTLCache(maxsize=1000, ttl=3600)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your frontend URL
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)

class ProfileData(BaseModel):
    username: str
    followers: int
    following: int
    dp: str
    is_verified: bool
    posts_count: int
    bio: str = None
    full_name: str = None

class ErrorResponse(BaseModel):
    error: str
    details: str = None

def get_instagram_client():
    """Initialize and return Instaloader client with proper settings"""
    L = instaloader.Instaloader(
        quiet=True,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        max_connection_attempts=3
    )
    
    # Configure to avoid rate limits
    L.request_timeout = 30
    L.sleep = True
    L.save_metadata = False
    L.download_comments = False
    L.download_geotags = False
    L.download_pictures = False
    
    return L

@app.get("/instagram/{username}", 
         response_model=ProfileData,
         responses={
             404: {"model": ErrorResponse, "description": "Profile not found"},
             429: {"model": ErrorResponse, "description": "Too many requests"},
             500: {"model": ErrorResponse, "description": "Internal server error"}
         })
@limiter.limit("10/minute")
async def get_instagram_profile(request: Request, username: str):
    """
    Fetch Instagram profile details by username
    
    - **username**: Instagram username to lookup (without @)
    - Returns: Profile data including DP URL, followers count, verification status
    """
    try:
        # Check cache first
        if username.lower() in profile_cache:
            logger.info(f"Serving from cache: {username}")
            return profile_cache[username.lower()]
        
        logger.info(f"Fetching fresh data for: {username}")
        start_time = time.time()
        
        L = get_instagram_client()
        
        try:
            profile = instaloader.Profile.from_username(L.context, username.lower())
        except instaloader.exceptions.ProfileNotExistsException:
            raise HTTPException(
                status_code=404,
                detail={"error": "Profile not found", "details": f"@{username} doesn't exist on Instagram"}
            )
        
        # Ensure we have fresh data
        if not profile.userid:
            raise HTTPException(
                status_code=404,
                detail={"error": "Profile not found", "details": "Invalid profile data received"}
            )
        
        # Prepare response data
        profile_data = ProfileData(
            username=profile.username,
            followers=profile.followers,
            following=profile.followees,
            dp=profile.profile_pic_url,
            is_verified=profile.is_verified,
            posts_count=profile.mediacount,
            bio=profile.biography,
            full_name=profile.full_name
        )
        
        # Cache the result
        profile_cache[username.lower()] = profile_data
        
        logger.info(f"Fetched profile in {time.time()-start_time:.2f}s: {username}")
        return profile_data
        
    except instaloader.exceptions.ProfileNotExistsException as e:
        logger.error(f"Profile not found: {username} - {str(e)}")
        raise HTTPException(
            status_code=404,
            detail={"error": "Profile not found", "details": str(e)}
        )
    except instaloader.exceptions.ConnectionException as e:
        logger.error(f"Connection error for {username}: {str(e)}")
        raise HTTPException(
            status_code=503,
            detail={"error": "Instagram connection failed", "details": "Instagram may be blocking requests. Try again later."}
        )
    except instaloader.exceptions.InstaloaderException as e:
        logger.error(f"Instaloader error for {username}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail={"error": "Instagram data fetch failed", "details": str(e)}
        )
    except Exception as e:
        logger.error(f"Unexpected error for {username}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail={"error": "Internal server error", "details": str(e)}
        )

@app.get("/health")
async def health_check():
    """Service health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": time.time(),
        "cache_size": len(profile_cache)
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8000,
        timeout_keep_alive=60,
        
        log_config=None  # Use default logging
    )
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

# Add these lines
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")
@app.get("/", response_class=HTMLResponse)
async def serve_home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})
