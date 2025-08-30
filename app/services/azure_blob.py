import os
import uuid
import logging
from typing import Tuple, Optional
from fastapi import UploadFile
from app.config import settings

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Azure services
_blob_service: Optional[any] = None
_container_client: Optional[any] = None

try:
    from azure.storage.blob import BlobServiceClient, ContentSettings
    if settings.has_azure_storage:
        _blob_service = BlobServiceClient.from_connection_string(settings.azure_storage_connection_string)
        _container_client = _blob_service.get_container_client(settings.azure_storage_container)
        logger.info(f"Azure Blob service initialized for container: {settings.azure_storage_container}")
    else:
        logger.warning("Azure storage not configured, will use local fallback")
except ImportError:
    logger.warning("Azure SDK not installed, will use local fallback")
except Exception as e:
    logger.error(f"Failed to initialize Azure Blob service: {e}")


def ensure_container_exists() -> None:
    """Ensure Azure container exists, create if needed"""
    if _container_client is None:
        return
        
    try:
        _container_client.create_container()
        logger.info("Azure container created successfully")
    except Exception as e:
        # Container might already exist
        logger.info(f"Container creation result: {e}")


def upload_file_to_blob(file: UploadFile) -> Tuple[str, str]:
    """
    Upload file to Azure Blob Storage or local fallback
    Returns: (url_or_path, filename)
    """
    logger.info(f"Starting upload for file: {file.filename}")
    
    # Try Azure Blob first
    if _container_client is not None:
        try:
            return _upload_to_azure(file)
        except Exception as e:
            logger.error(f"Azure upload failed: {e}, falling back to local storage")
            return _upload_to_local(file)
    
    # Fallback to local storage
    return _upload_to_local(file)


def _upload_to_azure(file: UploadFile) -> Tuple[str, str]:
    """Upload file to Azure Blob Storage"""
    ensure_container_exists()
    
    # Generate unique blob name
    extension = ""
    if file.filename and "." in file.filename:
        extension = "." + file.filename.split(".")[-1]
    blob_name = f"uploads/{uuid.uuid4().hex}{extension}"
    
    # Get blob client
    blob_client = _container_client.get_blob_client(blob_name)
    
    # Set content type
    content_type = file.content_type or "application/octet-stream"
    content_settings = ContentSettings(content_type=content_type)
    
    # Read file content
    try:
        # Reset file pointer
        file.file.seek(0)
        data = file.file.read()
        logger.info(f"Read {len(data)} bytes for Azure upload")
    except Exception as e:
        raise RuntimeError(f"Failed to read file: {e}")
    
    # Upload to Azure
    try:
        blob_client.upload_blob(data, overwrite=True, content_settings=content_settings)
        logger.info(f"Successfully uploaded to Azure: {blob_name}")
    except Exception as e:
        raise RuntimeError(f"Azure upload failed: {e}")
    
    # Return blob URL and name
    blob_url = blob_client.url
    logger.info(f"Azure blob URL: {blob_url}")
    return blob_url, blob_name


def _upload_to_local(file: UploadFile) -> Tuple[str, str]:
    """Upload file to local storage as fallback"""
    # Create local uploads directory
    os.makedirs("./local_uploads", exist_ok=True)
    
    # Generate unique filename
    extension = ""
    if file.filename and "." in file.filename:
        extension = "." + file.filename.split(".")[-1]
    local_name = f"uploads_{uuid.uuid4().hex}{extension}"
    local_path = os.path.join("./local_uploads", local_name)
    
    # Read and write file
    try:
        file.file.seek(0)
        data = file.file.read()
        logger.info(f"Read {len(data)} bytes for local storage")
    except Exception as e:
        raise RuntimeError(f"Failed to read file: {e}")
    
    # Write to local file
    try:
        with open(local_path, "wb") as f:
            f.write(data)
        logger.info(f"Saved to local path: {local_path}")
    except Exception as e:
        raise RuntimeError(f"Failed to write local file: {e}")
    
    return local_path, local_name
