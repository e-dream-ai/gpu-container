import runpod
import json
import urllib.request
import urllib.parse
import time
import os
import requests
import base64
import boto3
from botocore.exceptions import ClientError
from io import BytesIO

# Time to wait between API check attempts in milliseconds
COMFY_API_AVAILABLE_INTERVAL_MS = 50
# Maximum number of API check attempts
COMFY_API_AVAILABLE_MAX_RETRIES = 500
# Time to wait between poll attempts in milliseconds
COMFY_POLLING_INTERVAL_MS = int(os.environ.get("COMFY_POLLING_INTERVAL_MS", 250))
# Maximum number of poll attempts
COMFY_POLLING_MAX_RETRIES = int(os.environ.get("COMFY_POLLING_MAX_RETRIES", 500))
# Percent step at which to log progress while waiting for image generation
PROGRESS_LOG_STEP = int(os.environ.get("PROGRESS_LOG_STEP", 10))
# Host where ComfyUI is running
COMFY_HOST = "127.0.0.1:8188"
# Enforce a clean state after each job is done
REFRESH_WORKER = os.environ.get("REFRESH_WORKER", "false").lower() == "true"


def validate_input(job_input):
    """
    Validates the input for the handler function.
    """
    if job_input is None:
        return None, "Please provide input"

    if isinstance(job_input, str):
        try:
            job_input = json.loads(job_input)
        except json.JSONDecodeError:
            return None, "Invalid JSON format in input"

    workflow = job_input.get("workflow")
    if workflow is None:
        return None, "Missing 'workflow' parameter"

    images = job_input.get("images")
    if images is not None:
        if not isinstance(images, list) or not all(
            "name" in image and "image" in image for image in images
        ):
            return (
                None,
                "'images' must be a list of objects with 'name' and 'image' keys",
            )

    return {"workflow": workflow, "images": images}, None


def check_server(url, retries=500, delay=50):
    """
    Check if a server is reachable via HTTP GET request
    """
    for i in range(retries):
        try:
            response = requests.get(url)
            if response.status_code == 200:
                print(f"runpod-worker-comfy - API is reachable")
                return True
        except requests.RequestException as e:
            pass

        time.sleep(delay / 1000)

    print(
        f"runpod-worker-comfy - Failed to connect to server at {url} after {retries} attempts."
    )
    return False


def upload_images(images):
    """
    Upload a list of base64 encoded images to the ComfyUI server
    """
    if not images:
        return {"status": "success", "message": "No images to upload", "details": []}

    responses = []
    upload_errors = []

    print(f"runpod-worker-comfy - image(s) upload")

    for image in images:
        name = image["name"]
        image_data = image["image"]
        blob = base64.b64decode(image_data)

        files = {
            "image": (name, BytesIO(blob), "image/png"),
            "overwrite": (None, "true"),
        }

        response = requests.post(f"http://{COMFY_HOST}/upload/image", files=files)
        if response.status_code != 200:
            upload_errors.append(f"Error uploading {name}: {response.text}")
        else:
            responses.append(f"Successfully uploaded {name}")

    if upload_errors:
        print(f"runpod-worker-comfy - image(s) upload with errors")
        return {
            "status": "error",
            "message": "Some images failed to upload",
            "details": upload_errors,
        }

    print(f"runpod-worker-comfy - image(s) upload complete")
    return {
        "status": "success",
        "message": "All images uploaded successfully",
        "details": responses,
    }


def queue_workflow(workflow):
    """
    Queue a workflow to be processed by ComfyUI
    """
    data = json.dumps({"prompt": workflow}).encode("utf-8")
    req = urllib.request.Request(f"http://{COMFY_HOST}/prompt", data=data)
    return json.loads(urllib.request.urlopen(req).read())


def get_history(prompt_id):
    """
    Retrieve the history of a given prompt using its ID
    """
    try:
        with urllib.request.urlopen(f"http://{COMFY_HOST}/history/{prompt_id}", timeout=5) as response:
            return json.loads(response.read())
    except:
        return {}


def upload_to_r2(job_id: str, image_path: str) -> dict:
    """
    Upload a file to Cloudflare R2 and return a pre-signed URL with metadata.
    """
    try:
        endpoint_url = os.environ.get("R2_ENDPOINT_URL")
        access_key_id = os.environ.get("R2_ACCESS_KEY_ID")
        secret_access_key = os.environ.get("R2_SECRET_ACCESS_KEY")
        bucket_name = os.environ.get("R2_BUCKET_NAME")
        upload_directory = os.environ.get("R2_UPLOAD_DIRECTORY", "").strip().strip("/")
        expires_in = int(os.environ.get("R2_PRESIGNED_EXPIRY", "86400"))
        public_url_base = os.environ.get("R2_PUBLIC_URL_BASE")

        if not all([endpoint_url, access_key_id, secret_access_key, bucket_name]):
            raise Exception(
                "Missing R2 configuration. Please set R2_ENDPOINT_URL, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, and R2_BUCKET_NAME environment variables."
            )

        s3_client = boto3.client(
            's3',
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name='auto',
            config=boto3.session.Config(s3={"addressing_style": "path"})
        )

        filename = os.path.basename(image_path)
        name, ext = os.path.splitext(filename)
        ext_lower = ext.lower()
        unique_filename = f"{job_id}-{name}{ext_lower}"
        s3_key = f"{upload_directory}/{unique_filename}" if upload_directory else unique_filename

        content_type = "application/octet-stream"
        if ext_lower == ".png":
            content_type = "image/png"
        elif ext_lower in (".jpg", ".jpeg"):
            content_type = "image/jpeg"
        elif ext_lower == ".gif":
            content_type = "image/gif"
        elif ext_lower == ".mp4":
            content_type = "video/mp4"

        with open(image_path, 'rb') as file:
            s3_client.upload_fileobj(
                file,
                bucket_name,
                s3_key,
                ExtraArgs={'ContentType': content_type}
            )

        try:
            presigned_url = s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': bucket_name, 'Key': s3_key},
                ExpiresIn=expires_in
            )
            return {
                "url": presigned_url,
                "s3_key": s3_key,
                "bucket": bucket_name,
                "expires_in": expires_in
            }
        except Exception:
            if public_url_base:
                fallback_url = f"{public_url_base.rstrip('/')}/{s3_key}"
            else:
                account_id = endpoint_url.split('://')[1].split('.')[0]
                fallback_url = f"https://{account_id}.r2.dev/{s3_key}"
            return {
                "url": fallback_url,
                "s3_key": s3_key,
                "bucket": bucket_name
            }

    except ClientError as e:
        raise Exception(f"Failed to upload to R2: {str(e)}")
    except Exception as e:
        raise Exception(f"R2 upload error: {str(e)}")


def base64_encode(img_path):
    """
    Returns base64 encoded image.
    """
    with open(img_path, "rb") as image_file:
        encoded_string = base64.b64encode(image_file.read()).decode("utf-8")
        return f"{encoded_string}"


def get_output_image_path(outputs):
    """
    Returns an image or video path, preferring video
    """
    output_images = {}

    for node_id, node_output in outputs.items():
        print(f"node_output: {node_output}")
        if "gifs" in node_output:
            for video in node_output["gifs"]:
                output_images = os.path.join(video["subfolder"], video["filename"])
                return output_images
        if "images" in node_output:
            for image in node_output["images"]:
                output_images = os.path.join(image["subfolder"], image["filename"])

    return output_images


def process_output_images(outputs, job_id):
    """
    Process the outputs and return the image as URL or base64
    """
    COMFY_OUTPUT_PATH = os.environ.get("COMFY_OUTPUT_PATH", "/comfyui/output")

    output_images = get_output_image_path(outputs)

    print(f"runpod-worker-comfy - image generation is done (100%)")

    local_image_path = f"{COMFY_OUTPUT_PATH}/{output_images}"

    print(f"runpod-worker-comfy - {local_image_path}")

    if os.path.exists(local_image_path):
        if os.environ.get("R2_ENDPOINT_URL"):
            try:
                meta = upload_to_r2(job_id, local_image_path)
                image = meta.get("url")
                print(
                    "runpod-worker-comfy - the image was generated and uploaded to Cloudflare R2"
                )
            except Exception as e:
                print(f"runpod-worker-comfy - R2 upload failed: {str(e)}")
                return {
                    "status": "error",
                    "message": f"Failed to upload to R2: {str(e)}",
                }
        else:
            image = base64_encode(local_image_path)
            print(
                "runpod-worker-comfy - the image was generated and converted to base64"
            )

        result = {"status": "success", "message": image}
        if os.environ.get("R2_ENDPOINT_URL"):
            result.update({
                "s3_key": meta.get("s3_key"),
                "bucket": meta.get("bucket"),
                "expires_in": meta.get("expires_in")
            })
            result["video"] = image
        return result
    else:
        print("runpod-worker-comfy - the image does not exist in the output folder")
        return {
            "status": "error",
            "message": f"the image does not exist in the specified output folder: {local_image_path}",
        }


def handler(job):
    """
    The main function that handles a job of generating an image.
    """
    job_input = job["input"]

    validated_data, error_message = validate_input(job_input)
    if error_message:
        return {"error": error_message}

    workflow = validated_data["workflow"]
    images = validated_data.get("images")

    check_server(
        f"http://{COMFY_HOST}",
        COMFY_API_AVAILABLE_MAX_RETRIES,
        COMFY_API_AVAILABLE_INTERVAL_MS,
    )

    upload_result = upload_images(images)

    if upload_result["status"] == "error":
        return upload_result

    # Queue the workflow
    try:
        queued_workflow = queue_workflow(workflow)
        prompt_id = queued_workflow["prompt_id"]
        print(f"runpod-worker-comfy - queued workflow with ID {prompt_id}")
    except Exception as e:
        return {"error": f"Error queuing workflow: {str(e)}"}

    # Poll for completion with proper progress tracking
    print(f"runpod-worker-comfy - wait until image generation is complete")
    
    retries = 0
    last_logged_percent = -1
    
    try:
        while retries < COMFY_POLLING_MAX_RETRIES:
            # Check history to see if job is complete
            history = get_history(prompt_id)
            
            if prompt_id in history and history[prompt_id].get("outputs"):
                # Job completed successfully
                print(f"runpod-worker-comfy - image generation is done (100%)")
                break
            
            # Calculate simple time-based progress (0-99%)
            percent = min(99, int((retries / COMFY_POLLING_MAX_RETRIES) * 100))
            
            # Only update if progress changed
            if percent != last_logged_percent:
                runpod.serverless.progress_update(job, percent)
                last_logged_percent = percent
                if percent % PROGRESS_LOG_STEP == 0 or percent == 1:
                    print(f"runpod-worker-comfy - progress: {percent}%")

            time.sleep(COMFY_POLLING_INTERVAL_MS / 1000)
            retries += 1
        else:
            return {"error": "Max retries reached while waiting for image generation"}
            
    except Exception as e:
        return {"error": f"Error waiting for image generation: {str(e)}"}

    # Get the generated image
    images_result = process_output_images(history[prompt_id].get("outputs"), job["id"])

    result = {**images_result, "refresh_worker": REFRESH_WORKER}

    return result


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})