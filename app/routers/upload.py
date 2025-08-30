import json
import tempfile
import logging
import os
from typing import Any, Optional
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, BackgroundTasks, Query
from sqlalchemy.orm import Session
from app.services.azure_blob import upload_file_to_blob
from app.services.pdf_processor import extract_json_from_pdf
from app.db.session import SessionLocal
from app.db import models
from app.schemas import DocumentCreateResponse, DocumentDetailResponse

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def process_and_update(doc_id: int, tmp_path: str):
    """Background task to process PDF and update database"""
    logger.info(f"Starting background processing for document {doc_id}")
    logger.info(f"Temp file path: {tmp_path}")
    
    # Verify temp file exists
    if not os.path.exists(tmp_path):
        logger.error(f"Temp file not found: {tmp_path}")
        return
    
    db = SessionLocal()
    
    try:
        doc = db.get(models.Document, doc_id)
        if not doc:
            logger.error(f"Document {doc_id} not found in database")
            return
        
        try:
            logger.info(f"Processing PDF for document {doc_id}")
            # Process PDF and extract JSON
            json_str = extract_json_from_pdf(tmp_path)
            logger.info(f"PDF processing completed, JSON length: {len(json_str)}")
            
            json_data: Any = json.loads(json_str)
            logger.info(f"JSON parsing successful")
            
            # Update document status
            doc.json_data = json_data
            doc.status = "completed"
            doc.error_message = None
            
            db.add(doc)
            db.commit()
            logger.info(f"Document {doc_id} processed successfully")
            
        except Exception as e:
            logger.error(f"PDF processing failed for document {doc_id}: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            
            doc.status = "failed"
            doc.error_message = str(e)
            db.add(doc)
            db.commit()
            
    except Exception as e:
        logger.error(f"Database error during background processing: {e}")
        import traceback
        logger.error(f"Database error traceback: {traceback.format_exc()}")
    finally:
        db.close()
        # Clean up temp file
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
                logger.info(f"Cleaned up temp file: {tmp_path}")
            else:
                logger.warning(f"Temp file already removed: {tmp_path}")
        except Exception as e:
            logger.warning(f"Failed to clean up temp file: {e}")


@router.post("/upload", response_model=DocumentCreateResponse)
async def upload_document(
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = None,  # type: ignore
    db: Session = Depends(get_db),
    run_in_background: bool = Query(default=False, description="Set to true for background processing")
):
    """Upload PDF document and process it"""
    logger.info(f"Starting upload for file: {file.filename}")
    logger.info(f"Background processing: {run_in_background}")
    
    # Validate file type
    if not file.filename or not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")
    
    # Create temporary file for PDF processing
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            content = await file.read()
            if len(content) == 0:
                raise HTTPException(status_code=400, detail="Uploaded file is empty")
            
            tmp.write(content)
            tmp_path = tmp.name
            logger.info(f"Created temp file: {tmp_path} with {len(content)} bytes")
        
        # Upload to blob storage (Azure or local fallback)
        try:
            blob_url, blob_name = upload_file_to_blob(file)
            logger.info(f"File uploaded to blob: {blob_url}")
        except Exception as e:
            logger.error(f"Blob upload failed: {e}")
            raise HTTPException(status_code=500, detail=f"File upload failed: {e}")

        # Create database record
        doc = models.Document(
            filename=file.filename or blob_name, 
            blob_url=blob_url, 
            status="processing"
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        logger.info(f"Created document record with ID: {doc.id}")

        # Process PDF (background or synchronous)
        if run_in_background and background_tasks is not None:
            logger.info("Adding background task for PDF processing")
            background_tasks.add_task(process_and_update, doc_id=doc.id, tmp_path=tmp_path)
            return DocumentCreateResponse(
                id=doc.id, 
                filename=doc.filename, 
                blob_url=doc.blob_url, 
                status=doc.status
            )

        # Synchronous processing (default for UI)
        logger.info("Processing PDF synchronously")
        try:
            json_str = extract_json_from_pdf(tmp_path)
            logger.info(f"PDF processing completed, JSON length: {len(json_str)}")
            
            json_data: Any = json.loads(json_str)
            logger.info("JSON parsing successful")
            
            doc.json_data = json_data
            doc.status = "completed"
            doc.error_message = None
            
            db.add(doc)
            db.commit()
            db.refresh(doc)
            
            logger.info("PDF processing completed successfully")
            
        except Exception as e:
            logger.error(f"PDF processing failed: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            
            doc.status = "failed"
            doc.error_message = str(e)
            db.add(doc)
            db.commit()
            db.refresh(doc)
            raise HTTPException(status_code=500, detail=f"PDF processing failed: {e}")

        return DocumentCreateResponse(
            id=doc.id, 
            filename=doc.filename, 
            blob_url=doc.blob_url, 
            status=doc.status
        )

    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Unexpected error during upload: {e}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")
    finally:
        # Clean up temp file if synchronous processing
        if tmp_path and not run_in_background:
            try:
                os.remove(tmp_path)
                logger.info(f"Cleaned up temp file: {tmp_path}")
            except Exception as e:
                logger.warning(f"Failed to clean up temp file: {e}")


@router.get("/documents/{doc_id}", response_model=DocumentDetailResponse)
def get_document(doc_id: int, db: Session = Depends(get_db)):
    """Retrieve document by ID"""
    doc = db.get(models.Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    return DocumentDetailResponse(
        id=doc.id, 
        filename=doc.filename, 
        blob_url=doc.blob_url, 
        status=doc.status, 
        json_data=doc.json_data, 
        error_message=doc.error_message
    )


@router.post("/documents/{doc_id}/process")
def process_document_manually(doc_id: int, db: Session = Depends(get_db)):
    """Manually trigger PDF processing for a document"""
    logger.info(f"Manual processing requested for document {doc_id}")
    
    doc = db.get(models.Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    if doc.status == "completed":
        return {"message": "Document already processed", "status": doc.status}
    
    if doc.status == "failed":
        logger.info(f"Retrying failed document {doc_id}")
    
    try:
        # Get the PDF from blob storage or local path
        pdf_path = doc.blob_url
        if pdf_path.startswith("http"):
            # Azure blob - download temporarily
            import requests
            response = requests.get(pdf_path)
            if response.status_code != 200:
                raise HTTPException(status_code=500, detail="Failed to download from blob")
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(response.content)
                tmp_path = tmp.name
        else:
            # Local path
            tmp_path = pdf_path
        
        # Process the PDF
        json_str = extract_json_from_pdf(tmp_path)
        json_data: Any = json.loads(json_str)
        
        # Update document
        doc.json_data = json_data
        doc.status = "completed"
        doc.error_message = None
        
        db.add(doc)
        db.commit()
        
        # Clean up temp file if we created one
        if pdf_path.startswith("http") and os.path.exists(tmp_path):
            os.remove(tmp_path)
        
        logger.info(f"Manual processing completed for document {doc_id}")
        return {"message": "Processing completed", "status": "completed"}
        
    except Exception as e:
        logger.error(f"Manual processing failed for document {doc_id}: {e}")
        doc.status = "failed"
        doc.error_message = str(e)
        db.add(doc)
        db.commit()
        
        raise HTTPException(status_code=500, detail=f"Processing failed: {e}")


@router.post("/test-ocr")
async def test_ocr(file: UploadFile = File(...)):
    """Test OCR functionality on a PDF file"""
    logger.info(f"Testing OCR on file: {file.filename}")
    
    if not file.filename or not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")
    
    # Create temporary file
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            content = await file.read()
            if len(content) == 0:
                raise HTTPException(status_code=400, detail="Uploaded file is empty")
            
            tmp.write(content)
            tmp_path = tmp.name
            logger.info(f"Created temp file: {tmp_path} with {len(content)} bytes")
        
        # Test PDF type detection
        from app.services.pdf_processor import is_digital_pdf
        is_digital = is_digital_pdf(tmp_path)
        
        # Test OCR extraction
        from app.services.pdf_processor import scanned_pdf_content, digital_pdf_content
        
        if is_digital:
            logger.info("PDF detected as digital")
            extracted_text = digital_pdf_content(tmp_path)
            extraction_method = "digital"
        else:
            logger.info("PDF detected as scanned")
            extracted_text = scanned_pdf_content(tmp_path)
            extraction_method = "scanned"
        
        # Return diagnostic information
        return {
            "filename": file.filename,
            "file_size_bytes": len(content),
            "pdf_type": "digital" if is_digital else "scanned",
            "extraction_method": extraction_method,
            "extracted_text_length": len(extracted_text),
            "extracted_text_preview": extracted_text[:500] + "..." if len(extracted_text) > 500 else extracted_text,
            "ocr_status": "success" if extracted_text and len(extracted_text.strip()) > 10 else "failed"
        }
        
    except Exception as e:
        logger.error(f"OCR test failed: {e}")
        raise HTTPException(status_code=500, detail=f"OCR test failed: {e}")
    finally:
        # Clean up temp file
        if tmp_path:
            try:
                os.remove(tmp_path)
                logger.info(f"Cleaned up temp file: {tmp_path}")
            except Exception as e:
                logger.warning(f"Failed to clean up temp file: {e}")
