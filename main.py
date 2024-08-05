from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.responses import JSONResponse, Response, FileResponse
from pydantic import BaseModel, HttpUrl
import requests
from io import BytesIO
from pypdf import PdfReader, PdfWriter, generic, ObjectDeletionFlag
import pypdfium2 as pdfium
from PIL import Image
import os
import logging
from typing import Optional, cast

logger = logging.getLogger('uvicorn.error')
logger.setLevel(logging.DEBUG)

app = FastAPI()


OPS = {
    0: ObjectDeletionFlag.NONE,
    1: ObjectDeletionFlag.TEXT,
}

# Check enum equivalences at startup
for k, v in OPS.items():
    assert k == v, f"op {k} != {v}"

OUTPUT_MEDIA_TYPES = {
    0: 'application/pdf',
    1: 'image/png',
    2: 'image/jpeg',
}

DEFAULT_OP = 1
DEFAULT_TYPE = 0
DEFAULT_IMAGE_SCALE = 2


class ProcessRequest(BaseModel):
    file_url: HttpUrl
    op: Optional[int] = DEFAULT_OP
    type: Optional[int] = DEFAULT_TYPE


@app.get("/")
async def root():
    return {"message": "Server running"}

@app.get("/test")
async def test_page():
    return FileResponse('test.html')

def concat_images(images):
    widths, heights = zip(*(image.size for image in images))
    total_height = sum(heights)
    max_width = max(widths)
    
    concat_image = Image.new('RGBA', (max_width, total_height), color=(0,0,0,0))
    
    y_offset = 0
    x_offset = 0
    for image in images:
        x_offset = 0 if image.size[0] == max_width else (max_width - image.size[0]) // 2 # centered
        concat_image.paste(image, (x_offset, y_offset))
        y_offset += image.size[1]

    return concat_image


def process_pdf(file_url: str, op: int = DEFAULT_OP, type: int = DEFAULT_TYPE):
    logger.debug(f"Processing '{file_url}'")
    logger.debug(f"Operation  : {repr(ObjectDeletionFlag(op))}")
    logger.debug(f"Output Type: {OUTPUT_MEDIA_TYPES[type]}")
    
    # Check if the input file has a .pdf extension
    file_url = str(file_url) # force-convert to str
    if not file_url.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Input file must have a .pdf extension")

    # Fetch the PDF file from the URL
    response = requests.get(file_url)
    response.raise_for_status()
    pdf_content = response.content

    # Create a PdfReader object from the fetched content
    pdf_reader = PdfReader(BytesIO(pdf_content))

    # Create a PdfWriter object
    pdf_writer = PdfWriter()

    # Add pages to writer
    for page in pdf_reader.pages:
        pdf_writer.add_page(page)

    # Add metadata (if any)
    if pdf_reader.metadata is not None:
        pdf_writer.add_metadata(pdf_reader.metadata)

    # Process and compress pages
    for page in pdf_writer.pages:
        pdf_writer.remove_objects_from_page(page=page, to_delete=ObjectDeletionFlag(op))
        page.compress_content_streams()  # This might be CPU intensive!

    # Write the result to a BytesIO object
    output = BytesIO()
    pdf_writer.write(output)
    output.seek(0)

    if type in [1, 2]: # image
        # Load a document using pdfium
        pdf = pdfium.PdfDocument(output)
        # Loop over pages and render
        images = []
        for page_index, page in enumerate(pdf):
            image = page.render(scale=DEFAULT_IMAGE_SCALE).to_pil()
            images.append(image)
        
        output_image = concat_images(images)
        
        # Write the result to a BytesIO object
        output = BytesIO()
        if type == 1:
            output_image.save(output, format='PNG')
        else:
            output_image.convert('RGB').save(output, format='JPEG', quality=90)
            
        output.seek(0)
        
    # Generate the output filename
    input_filename = os.path.basename(file_url)
    # output_filename = os.path.splitext(input_filename)[0] + '_processed.pdf'

    return output, input_filename

@app.post("/clean_pdf")
async def clean_pdf_post(request: ProcessRequest):
    if not request:
        raise HTTPException(status_code=400, detail="Missing JSON payload. Please provide 'file_url' in the request body.")
    return process_request(request.file_url, request.op, request.type)

@app.get("/clean_pdf")
async def clean_pdf_get(file_url: str, op: int = DEFAULT_OP, type: int = DEFAULT_TYPE):
    if file_url is None:
        raise HTTPException(status_code=400, detail="Missing 'file_url' parameter in the query string.")
    if op is None:
        raise HTTPException(status_code=400, detail="Missing 'op' parameter in the query string.")
    return process_request(file_url, op, type)

def process_request(file_url: str, op: int = DEFAULT_OP, type: int = DEFAULT_TYPE):
    try:
        output, input_filename = process_pdf(file_url, op=op, type=type)

        # Return the PDF as a downloadable file along with the response message
        media_type = OUTPUT_MEDIA_TYPES[type]
        headers = {
            # "Content-Disposition": f"attachment; filename={output_filename}",
            "Content-Type": media_type,
        }

        # Prepare the response message
        response_data = Response(
            content=output.getvalue(),
            media_type=media_type,
            headers=headers,
        )

        return response_data

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)