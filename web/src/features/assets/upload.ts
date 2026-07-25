import type { Asset, AssetUploadTicket, Thread } from "../../domain/types";
import type { ApiClient } from "../../shared/api/api-client";

async function sha256(file: File): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return [...new Uint8Array(digest)]
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
}

function canvasBlob(
  canvas: HTMLCanvasElement,
  type: string,
  quality: number,
): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error("Image encoding failed"))),
      type,
      quality,
    );
  });
}

async function normalizeImage(file: File): Promise<File> {
  if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) return file;
  const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  let scale = Math.min(1, 2048 / Math.max(bitmap.width, bitmap.height));
  const outputType = file.type === "image/jpeg" ? "image/jpeg" : "image/webp";
  let best: Blob = file;
  try {
    for (let attempt = 0; attempt < 8; attempt += 1) {
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(bitmap.width * scale));
      canvas.height = Math.max(1, Math.round(bitmap.height * scale));
      const context = canvas.getContext("2d", { alpha: outputType !== "image/jpeg" });
      if (!context) throw new Error("Image processing is unavailable");
      if (outputType === "image/jpeg") {
        context.fillStyle = "#fff";
        context.fillRect(0, 0, canvas.width, canvas.height);
      }
      context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      for (const quality of [0.86, 0.78, 0.7, 0.62]) {
        const candidate = await canvasBlob(canvas, outputType, quality);
        if (candidate.size < best.size) best = candidate;
        if (candidate.size <= 450_000) {
          const stem = file.name.replace(/\.[^.]+$/, "") || "upload";
          const extension = outputType === "image/jpeg" ? ".jpg" : ".webp";
          return new File([candidate], `${stem}${extension}`, { type: outputType });
        }
      }
      scale *= 0.82;
    }
  } finally {
    bitmap.close();
  }
  const stem = file.name.replace(/\.[^.]+$/, "") || "upload";
  const extension = outputType === "image/jpeg" ? ".jpg" : ".webp";
  return new File([best], `${stem}${extension}`, { type: outputType });
}

async function uploadToSignedTicket(ticket: AssetUploadTicket, file: File): Promise<void> {
  if (file.size <= 6 * 1024 * 1024) {
    const uploadBody = new FormData();
    uploadBody.append("cacheControl", "3600");
    uploadBody.append("file", file);
    const response = await fetch(ticket.signed_url, {
      method: "PUT",
      body: uploadBody,
    });
    if (!response.ok) throw new Error("Storage upload failed");
    return;
  }

  const { Upload } = await import("tus-js-client");
  await new Promise<void>((resolve, reject) => {
    const upload = new Upload(file, {
      endpoint: ticket.tus_endpoint,
      retryDelays: [0, 3000, 5000, 10_000, 20_000],
      headers: { "x-signature": ticket.token },
      uploadDataDuringCreation: true,
      removeFingerprintOnSuccess: true,
      metadata: {
        bucketName: ticket.asset.bucket_id,
        objectName: ticket.asset.object_path,
        contentType: file.type || "application/octet-stream",
        cacheControl: "3600",
      },
      chunkSize: 6 * 1024 * 1024,
      onError: reject,
      onSuccess: () => resolve(),
    });
    upload.findPreviousUploads().then((previous) => {
      if (previous.length) upload.resumeFromPreviousUpload(previous[0]);
      upload.start();
    }, reject);
  });
}

export async function uploadAsset(
  client: ApiClient,
  thread: Thread,
  selectedFile: File,
): Promise<Asset> {
  const file = await normalizeImage(selectedFile);
  const checksum = await sha256(file);
  const ticket = await client.request<AssetUploadTicket>(
    `/api/threads/${thread.id}/assets/uploads`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        filename: file.name,
        media_type: file.type || "application/octet-stream",
        size_bytes: file.size,
        sha256: checksum,
      }),
    },
  );
  await uploadToSignedTicket(ticket, file);
  return client.request<Asset>(`/api/assets/${ticket.asset.id}/complete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sha256: checksum }),
  });
}
