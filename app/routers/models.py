import os
import base64
import asyncio
import time
import httpx
from io import BytesIO
from PIL import Image
from fastapi import APIRouter, UploadFile, File, HTTPException
from typing import Optional, List, Union, Dict

from app.backend.config import settings
from app.models.gpt_vision import TruckViewClassifier as GPTVisionTruckViewClassifier
from app.models.open_clip import TruckViewClassifier as OpenClipTruckOrientationClassifier
from app.utils.folder_utils import clean_folder
router = APIRouter()


# Instantiate the classifiers once (so models are not reloaded each call)
open_clip_classifier = OpenClipTruckOrientationClassifier()
gpt_vision_classifier = GPTVisionTruckViewClassifier()

# Define a directory to store uploaded images
UPLOAD_DIRECTORY = "app/database/uploaded_truck_images"
os.makedirs(UPLOAD_DIRECTORY, exist_ok=True)


@router.post("/open_clip-test")
async def image_quality_analyzer(files: List[UploadFile] = File(...)):
    """
        Upload multiple image files and test OpenCLIP cab orientation detection.
    """
    clean_folder(UPLOAD_DIRECTORY)
    uploaded_filenames = []
    for file in files:
        try:
            file_path = os.path.join(UPLOAD_DIRECTORY, file.filename)
            with open(file_path, "wb") as buffer:
                buffer.write(await file.read())
            uploaded_filenames.append(file.filename)
        except Exception as e:
            print(f"Error uploading {file.filename}: {e}")

    if not uploaded_filenames:
        raise HTTPException(status_code=500, detail="No files were uploaded successfully.")

    print({"message": f"Successfully uploaded: {', '.join(uploaded_filenames)}"})

    valid_extensions = ('.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.webp')
    images = [
        os.path.join(UPLOAD_DIRECTORY, file_name)
        for file_name in uploaded_filenames
        if file_name.lower().endswith(valid_extensions)
    ]

    if not images:
        raise HTTPException(status_code=400, detail="No valid images found")

    results = []
    for image_path in images:
        orientation = open_clip_classifier.classify_image(image_path)
        results.append({
            "filename": os.path.basename(image_path),
            "cab_orientation": orientation,
        })

    return {
        "message": "OpenCLIP cab orientation detection completed",
        "results": results,
    }

async def _fetch_image(
    client: httpx.AsyncClient,
    url: Union[str, Dict],
    url_no: int,
    download_headers: dict,
) -> Dict:
    """Fetch and decode a single image from a relative or absolute URL."""
    complete_url = None
    raw_url = url["url"] if isinstance(url, dict) else url

    try:
        if raw_url.startswith("http"):
            complete_url = raw_url
        else:
            complete_url = f"{settings.BASE_URL}{raw_url}"

        print(f"Truck View URL: {complete_url}")

        max_wait_time = 10.0
        retry_interval = 1.0
        start_time = time.monotonic()
        attempt = 0
        permanent_errors = {400, 401, 403, 404, 405, 410, 422}
        transient_errors = {429, 500, 502, 503, 504}

        while True:
            attempt += 1
            elapsed_time = time.monotonic() - start_time
            resp = await client.get(complete_url, headers=download_headers)

            if resp.status_code == 200:
                if attempt > 1:
                    print(f"Successfully fetched image after {elapsed_time:.1f}s (attempt {attempt})")
                break

            if resp.status_code in permanent_errors:
                error_message = f"HTTP {resp.status_code}"
                try:
                    if "application/json" in resp.headers.get("content-type", ""):
                        error_json = resp.json()
                        error_message = error_json.get("message", error_json.get("description", str(error_json)))
                except Exception:
                    pass
                return {
                    "image_index": url_no,
                    "image_url": complete_url,
                    "error": error_message,
                }

            if resp.status_code in transient_errors:
                if elapsed_time >= max_wait_time:
                    return {
                        "image_index": url_no,
                        "image_url": complete_url,
                        "error": f"Timeout after {elapsed_time:.1f}s - HTTP {resp.status_code}",
                    }
                print(f"Transient error {resp.status_code}, retrying in {retry_interval}s...")
                await asyncio.sleep(retry_interval)
                continue

            return {
                "image_index": url_no,
                "image_url": complete_url,
                "error": f"Unexpected HTTP {resp.status_code}",
            }

        content_type = resp.headers.get("content-type", "").lower()
        if "application/json" in content_type:
            try:
                error_json = resp.json()
                error_message = error_json.get("message", error_json.get("description", str(error_json)))
                return {
                    "image_index": url_no,
                    "image_url": complete_url,
                    "status_code": 200,
                    "error": f"Expected image but got JSON: {error_message}",
                }
            except Exception:
                pass

        try:
            image = Image.open(BytesIO(resp.content)).convert("RGB")
        except Exception:
            try:
                image_data = base64.b64decode(resp.content)
                image = Image.open(BytesIO(image_data)).convert("RGB")
            except Exception as e:
                return {
                    "image_index": url_no,
                    "image_url": complete_url,
                    "error": f"Could not parse image data as binary or base64: {str(e)}",
                }

        return {
            "image_index": url_no,
            "image_url": complete_url,
            "image": image,
        }
    except httpx.HTTPStatusError as e:
        print(f"HTTP error for {complete_url}: {e.response.status_code} - {e.response.text[:200]}")
        return {
            "image_index": url_no,
            "image_url": complete_url or str(raw_url),
            "error": f"HTTP {e.response.status_code}: {str(e)}",
        }
    except httpx.RequestError as e:
        print(f"Request error for {complete_url}: {str(e)}")
        return {
            "image_index": url_no,
            "image_url": complete_url or str(raw_url),
            "error": f"Request error: {str(e)}",
        }
    except Exception as e:
        print(f"Error processing {complete_url}: {str(e)}")
        return {
            "image_index": url_no,
            "image_url": complete_url or str(raw_url),
            "error": str(e),
        }


async def fetch_and_detect_orientation(
    client: httpx.AsyncClient,
    url: Union[str, Dict],
    url_no: int,
    download_headers: dict,
) -> dict:
    fetched = await _fetch_image(client, url, url_no, download_headers)
    if "error" in fetched:
        return fetched

    orientation = open_clip_classifier.classify_image(fetched["image"])
    return {
        "image_index": fetched["image_index"],
        "image_url": fetched["image_url"],
        "cab_orientation": orientation,
    }


async def fetch_and_classify_image(
    client: httpx.AsyncClient,
    url: Union[str, Dict],
    url_no: int,
    download_headers: dict,
) -> dict:
    fetched = await _fetch_image(client, url, url_no, download_headers)
    if "error" in fetched:
        return fetched

    orientation = open_clip_classifier.classify_image(fetched["image"])
    result = await gpt_vision_classifier.classify_image(
        fetched["image"],
        orientation_hint=orientation,
    )
    predicted_label = result["predicted_label"]
    print(f"Truck View Image Label: {predicted_label}")

    return {
        "image_index": fetched["image_index"],
        "image_url": fetched["image_url"],
        "predicted_label": predicted_label,
        "probabilities": result["probabilities"],
        "cab_orientation": orientation,
        "model": result.get("model"),
        "raw_response": result.get("raw_response"),
        "gpt_predicted_label": result.get("gpt_predicted_label"),
        "orientation_override_applied": result.get("orientation_override_applied", False),
        **({"error": result["error"]} if "error" in result else {}),
    }


@router.post("/open-clip")
async def image_quality_analyzer(image_urls: Union[List[str], List[Dict]]):
    """
    Detect truck cab orientation using OpenCLIP.
    Accepts a list of image URLs and returns left/right orientation hints.
    """
    if not image_urls:
        raise HTTPException(status_code=400, detail="No image URLs provided")

    download_headers = settings.HEADERS.copy()
    download_headers.pop("Content-Type", None)

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        tasks = [
            fetch_and_detect_orientation(client, url, url_no, download_headers)
            for url_no, url in enumerate(image_urls, start=1)
        ]
        orientation_results = await asyncio.gather(*tasks)

    return {
        "message": "OpenCLIP cab orientation detection completed",
        "results": orientation_results,
    }


@router.post("/gpt-vision")
async def image_quality_analyzer(image_urls: Union[List[str], List[Dict]]):
    """
    Classify truck images using GPT Vision with OpenCLIP cab-orientation guidance.
    """
    if not image_urls:
        raise HTTPException(status_code=400, detail="No image URLs provided")

    download_headers = settings.HEADERS.copy()
    download_headers.pop("Content-Type", None)

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        tasks = [
            fetch_and_classify_image(client, url, url_no, download_headers)
            for url_no, url in enumerate(image_urls, start=1)
        ]
        view_results = await asyncio.gather(*tasks)

    found_labels = {r["predicted_label"] for r in view_results if "predicted_label" in r}
    all_labels = set(gpt_vision_classifier.labels)
    missing_labels = list(all_labels - found_labels)

    return {
        "views_found": list(found_labels),
        "missing": missing_labels,
        "detailed_results": view_results,
    }


@router.post("/gpt-vision-test")
async def gpt_vision_test(files: List[UploadFile] = File(...)):
    clean_folder(UPLOAD_DIRECTORY)

    uploaded_filenames = []
    for file in files:
        try:
            file_path = os.path.join(UPLOAD_DIRECTORY, file.filename)
            with open(file_path, "wb") as buffer:
                buffer.write(await file.read())
            uploaded_filenames.append(file.filename)
        except Exception as e:
            print(f"Error uploading {file.filename}: {e}")

    if not uploaded_filenames:
        raise HTTPException(status_code=500, detail="No files were uploaded successfully.")

    valid_extensions = ('.jpg', '.jpeg', '.png', '.bmp', '.webp')
    images = [
        os.path.join(UPLOAD_DIRECTORY, file_name)
        for file_name in uploaded_filenames
        if file_name.lower().endswith(valid_extensions)
    ]

    if not images:
        raise HTTPException(status_code=400, detail="No valid images found")

    results = []
    for image_path in images:
        orientation = open_clip_classifier.classify_image(image_path)
        classification = await gpt_vision_classifier.classify_image(
            image_path,
            orientation_hint=orientation,
        )
        results.append({
            "filename": os.path.basename(image_path),
            "cab_orientation": orientation,
            "predicted_label": classification["predicted_label"],
            "probabilities": classification["probabilities"],
            "model": classification.get("model"),
            "raw_response": classification.get("raw_response"),
            "gpt_predicted_label": classification.get("gpt_predicted_label"),
            "orientation_override_applied": classification.get("orientation_override_applied", False),
            **({"error": classification["error"]} if "error" in classification else {}),
        })

    return {
        "message": "GPT Vision classification completed with OpenCLIP cab orientation guidance",
        "results": results,
    }
