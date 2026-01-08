import runpod
import json
import urllib.request
import time
import os
import requests
import base64
import boto3
from botocore.exceptions import ClientError
from io import BytesIO
import websocket
import uuid

# Configuration
COMFY_API_AVAILABLE_INTERVAL_MS = 50
COMFY_API_AVAILABLE_MAX_RETRIES = 500
COMFY_POLLING_INTERVAL_MS = int(os.environ.get("COMFY_POLLING_INTERVAL_MS", 250))
COMFY_POLLING_MAX_RETRIES = int(os.environ.get("COMFY_POLLING_MAX_RETRIES", 500))
PROGRESS_LOG_STEP = int(os.environ.get("PROGRESS_LOG_STEP", 10))
COMFY_HOST = "127.0.0.1:8188"
REFRESH_WORKER = os.environ.get("REFRESH_WORKER", "false").lower() == "true"


def validate_input(job_input):
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
    for i in range(retries):
        try:
            response = requests.get(url)
            if response.status_code == 200:
                print(f"runpod-worker-comfy - API is reachable")
                return True
        except requests.RequestException:
            pass
        time.sleep(delay / 1000)

    print(f"runpod-worker-comfy - Failed to connect to server at {url}")
    return False


def upload_images(images):
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
    data = json.dumps({"prompt": workflow}).encode("utf-8")
    req = urllib.request.Request(f"http://{COMFY_HOST}/prompt", data=data)
    return json.loads(urllib.request.urlopen(req).read())


def get_history(prompt_id):
    try:
        with urllib.request.urlopen(
            f"http://{COMFY_HOST}/history/{prompt_id}", timeout=5
        ) as response:
            return json.loads(response.read())
    except:
        return {}


def upload_to_r2(job_id: str, image_path: str) -> dict:
    try:
        endpoint_url = os.environ.get("R2_ENDPOINT_URL")
        access_key_id = os.environ.get("R2_ACCESS_KEY_ID")
        secret_access_key = os.environ.get("R2_SECRET_ACCESS_KEY")
        bucket_name = os.environ.get("R2_BUCKET_NAME")
        upload_directory = (
            os.environ.get("R2_UPLOAD_DIRECTORY", "").strip().strip("/")
        )
        expires_in = int(os.environ.get("R2_PRESIGNED_EXPIRY", "86400"))
        public_url_base = os.environ.get("R2_PUBLIC_URL_BASE")

        if not all([endpoint_url, access_key_id, secret_access_key, bucket_name]):
            raise Exception("Missing R2 configuration")

        s3_client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
            config=boto3.session.Config(s3={"addressing_style": "path"}),
        )

        filename = os.path.basename(image_path)
        name, ext = os.path.splitext(filename)
        ext_lower = ext.lower()
        unique_filename = f"{job_id}-{name}{ext_lower}"
        s3_key = (
            f"{upload_directory}/{unique_filename}"
            if upload_directory
            else unique_filename
        )

        content_type = "application/octet-stream"
        if ext_lower == ".png":
            content_type = "image/png"
        elif ext_lower in (".jpg", ".jpeg"):
            content_type = "image/jpeg"
        elif ext_lower == ".gif":
            content_type = "image/gif"
        elif ext_lower == ".mp4":
            content_type = "video/mp4"

        with open(image_path, "rb") as file:
            s3_client.upload_fileobj(
                file, bucket_name, s3_key, ExtraArgs={"ContentType": content_type}
            )

        try:
            presigned_url = s3_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket_name, "Key": s3_key},
                ExpiresIn=expires_in,
            )
            return {
                "url": presigned_url,
                "s3_key": s3_key,
                "bucket": bucket_name,
                "expires_in": expires_in,
            }
        except Exception:
            if public_url_base:
                fallback_url = f"{public_url_base.rstrip('/')}/{s3_key}"
            else:
                account_id = endpoint_url.split("://")[1].split(".")[0]
                fallback_url = f"https://{account_id}.r2.dev/{s3_key}"
            return {"url": fallback_url, "s3_key": s3_key, "bucket": bucket_name}

    except ClientError as e:
        raise Exception(f"Failed to upload to R2: {str(e)}")
    except Exception as e:
        raise Exception(f"R2 upload error: {str(e)}")


def base64_encode(img_path):
    with open(img_path, "rb") as image_file:
        encoded_string = base64.b64encode(image_file.read()).decode("utf-8")
        return f"{encoded_string}"


def get_output_image_path(outputs):
    output_images = {}

    for node_id, node_output in outputs.items():
        if "gifs" in node_output:
            for video in node_output["gifs"]:
                output_images = os.path.join(video["subfolder"], video["filename"])
                return output_images
        if "images" in node_output:
            for image in node_output["images"]:
                output_images = os.path.join(image["subfolder"], image["filename"])

    return output_images


def process_output_images(outputs, job_id):
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
                    "runpod-worker-comfy - the image was generated and uploaded to R2"
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
            result.update(
                {
                    "s3_key": meta.get("s3_key"),
                    "bucket": meta.get("bucket"),
                    "expires_in": meta.get("expires_in"),
                }
            )
            result["video"] = image
        return result
    else:
        print("runpod-worker-comfy - the image does not exist in the output folder")
        return {
            "status": "error",
            "message": f"Image does not exist: {local_image_path}",
        }


def handler(job):
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

    # Connect to WebSocket
    client_id = str(uuid.uuid4())
    ws = None

    try:
        ws = websocket.WebSocket()
        ws.settimeout(1)  # 1 second timeout for recv
        ws.connect(f"ws://{COMFY_HOST}/ws?clientId={client_id}")
        print(f"runpod-worker-comfy - WebSocket connected")
    except Exception as e:
        print(f"runpod-worker-comfy - WebSocket connection failed: {str(e)}")
        ws = None

    last_percent = 0

    try:
        while True:
            if ws:
                try:
                    out = ws.recv()
                    if isinstance(out, str):
                        message = json.loads(out)

                        # Handle progress updates
                        if message.get("type") == "progress":
                            data = message.get("data", {})
                            value = data.get("value", 0)
                            max_value = data.get("max", 100)

                            if max_value > 0:
                                percent = min(99, int((value / max_value) * 100))
                                if percent != last_percent:
                                    runpod.serverless.progress_update(job, percent)
                                    if percent % PROGRESS_LOG_STEP == 0:
                                        print(
                                            f"runpod-worker-comfy - progress: {percent}%"
                                        )
                                    last_percent = percent

                        # Check if execution is complete
                        elif message.get("type") == "executing":
                            data = message.get("data", {})
                            if (
                                data.get("node") is None
                                and data.get("prompt_id") == prompt_id
                            ):
                                print(
                                    f"runpod-worker-comfy - execution complete (100%)"
                                )
                                runpod.serverless.progress_update(job, 100)
                                break
                except websocket.WebSocketTimeoutException:
                    pass
            else:
                # Fallback: check history
                history = get_history(prompt_id)
                if prompt_id in history and history[prompt_id].get("outputs"):
                    print(f"runpod-worker-comfy - generation complete")
                    runpod.serverless.progress_update(job, 100)
                    break

                time.sleep(COMFY_POLLING_INTERVAL_MS / 1000)

    except Exception as e:
        return {"error": f"Error during execution: {str(e)}"}
    finally:
        if ws:
            ws.close()

    # Get the generated image
    history = get_history(prompt_id)
    if not (prompt_id in history and history[prompt_id].get("outputs")):
        return {"error": "No outputs found in history"}

    images_result = process_output_images(
        history[prompt_id].get("outputs"), job["id"]
    )

    result = {**images_result, "refresh_worker": REFRESH_WORKER}

    return result


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})