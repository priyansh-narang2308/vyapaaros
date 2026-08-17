"""Application configuration using pydantic-settings."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

                 
    app_name: str = "Agentic Commerce Middleware"
    app_version: str = "0.1.0"
    debug: bool = False
    log_level: str = "INFO"                               
    log_sql: bool = False                                           

              
    database_url: str = "sqlite:///./agentic_commerce.db"

                                                     
                                                                                  
    webhook_url: str = "http://localhost:3000/api/webhooks/acp"
    webhook_secret: str = "whsec_demo_secret"

                                                       
    merchant_api_key: str = ""

                            
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

                                 
    ucp_version: str = "2026-01-23"
    ucp_base_url: str | None = (
        None                                                       
    )
    ucp_business_name: str | None = None
    ucp_continue_url: str | None = None                                         
    ucp_order_webhook_url: str = "http://localhost:3000/api/webhooks/ucp"

                                                           
    ucp_signing_key_id: str = "ucp-key-1"
    ucp_signing_key_kty: str = "EC"                 
    ucp_signing_key_crv: str = "P-256"                        
    ucp_signing_key_alg: str = "ES256"                      
    ucp_signing_key_x: str = ""                                  
    ucp_signing_key_y: str = ""                                            

                                   
    promotion_agent_url: str = "http://localhost:8002"
    promotion_agent_timeout: float = 10.0                            

                                       
    post_purchase_agent_url: str = "http://localhost:8003"
    post_purchase_agent_timeout: float = 15.0           


@lru_cache
def get_settings() -> Settings:
    """Get cached application settings."""
    return Settings()
