import { useEffect, useId, useMemo, useRef, useState } from "react";
import { authHeaders } from "../../lib/auth/token";
import { editJsonValue, type MediaMode } from "./falMediaStudioModel";

/** User-approved file → Fal CDN URL. Raw bytes never enter canvas state/WS. */
export function FalMediaUploader({
  mode, operation, fields, argumentsJson, disabled, onJsonChange, onBusyChange,
}: {
  mode: MediaMode;
  operation: string;
  fields: string[];
  argumentsJson: string;
  disabled: boolean;
  onJsonChange: (json: string) => void;
  onBusyChange: (uploading: boolean) => void;
}) {
  const urlFieldListId = useId();
  const initialField = useMemo(() =>
    mode === "all" ? fields[0] || "" :
      operation === "image-to-image" || operation === "image-to-video" ? "image_url" :
      operation === "video-to-video" ? "video_url" :
        mode === "audio" ? "audio_url" : mode === "video" ? "video_url" : "image_url",
  [mode, operation, fields]);
  // Keep a user-customized parameter only within its matching workflow.
  // No setState-in-effect reset on modality changes: derive the default.
  const scope = `${mode}|${operation}`;
  const [fieldOverride, setFieldOverride] = useState<{ scope: string; field: string } | null>(null);
  const field = fieldOverride?.scope === scope ? fieldOverride.field : initialField;
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [message, setMessage] = useState("");
  const xhrRef = useRef<XMLHttpRequest | null>(null);
  useEffect(() => () => { xhrRef.current?.abort(); }, []);

  const selection = (next: File | undefined) => {
    setFile(next || null);
    setProgress(null);
    setMessage("");
  };
  const upload = () => {
    if (!file || disabled || uploading) return;
    if (file.size === 0 || file.size > 512 * 1024 * 1024) {
      setMessage("Select a non-empty media file no larger than 512 MiB.");
      return;
    }
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(field)) {
      setMessage("Enter a valid model parameter name, such as image_url.");
      return;
    }
    if (!window.confirm(
      `Send "${file.name}" (${(file.size / (1024 * 1024)).toFixed(2)} MiB) to the Fal.ai CDN? This transfers the file outside Graphlink. Do not upload private or sensitive data without consent.`
    )) return;

    const xhr = new XMLHttpRequest();
    xhrRef.current = xhr;
    setUploading(true);
    onBusyChange(true);
    setMessage("");
    setProgress(0);
    xhr.open("POST", "/api/fal-media/upload");
    xhr.responseType = "json";
    for (const [key, value] of Object.entries(authHeaders())) xhr.setRequestHeader(key, value);
    xhr.setRequestHeader("X-Fal-Media-Filename", encodeURIComponent(file.name));
    xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream");
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) {
        setProgress(Math.min(99, Math.round(event.loaded / event.total * 100)));
      }
    };
    const finish = () => {
      if (xhrRef.current !== xhr) return;
      xhrRef.current = null;
      setUploading(false);
      onBusyChange(false);
    };
    xhr.onload = () => {
      if (xhrRef.current !== xhr) return;
      const result: unknown = xhr.response;
      const data = result && typeof result === "object" ? result as Record<string, unknown> : {};
      if (xhr.status < 200 || xhr.status >= 300) {
        setMessage(typeof data.error === "string" ? data.error : "Upload failed. Check the API key and Fal SDK.");
        finish();
        return;
      }
      const url = typeof data.url === "string" ? data.url : "";
      if (!url.startsWith("https://")) {
        setMessage("The media service returned no valid HTTPS URL.");
        finish();
        return;
      }
      // The editor state stores only this URL, not the original file bytes.
      const nextJson = editJsonValue(argumentsJson, field, url);
      if (!nextJson) {
        setMessage("Fix Advanced model JSON before attaching uploaded media.");
        finish();
        return;
      }
      onJsonChange(nextJson);
      setProgress(100);
      setMessage(`Uploaded successfully. The ${field} parameter now points to Fal CDN.`);
      setFile(null);
      finish();
    };
    xhr.onerror = () => {
      if (xhrRef.current === xhr) setMessage("Network error while uploading. Check Fal CDN for a completed upload before retrying.");
      finish();
    };
    xhr.onabort = () => {
      if (xhrRef.current === xhr) setMessage("Upload stopped. The remote service may have received some bytes.");
      finish();
    };
    xhr.send(file);
  };
  return (
    <div className="fal-studio-uploader" aria-label="Reference media upload">
      <div className="fal-studio-uploader-heading">
        <span className="fal-studio-kicker">REFERENCE MEDIA</span>
        <strong>Bring your own source</strong>
      </div>
      <p className="fal-studio-quiet">
        Upload a reference file to the Fal CDN and attach its URL to a model input.
      </p>
      <div className="fal-studio-uploader-grid">
        <label className="fal-studio-field">
          <span>Source file</span>
          <input type="file" accept={mode === "all" ? "image/*,audio/*,video/*,.glb,.gltf,.obj,.ply,.stl,.fbx,.usdz,.pdf,.zip,.csv,.srt,.vtt" : "image/*,audio/*,video/*"} disabled={disabled || uploading}
            onChange={(event) => selection(event.target.files?.[0])}
            className="fal-studio-file-input nodrag nopan" />
        </label>
        <label className="fal-studio-field">
          <span>Fal input field</span>
          <input className="fal-studio-control nodrag nopan" disabled={disabled || uploading}
            list={urlFieldListId} value={field}
            aria-label="Media URL parameter"
            onChange={(event) => setFieldOverride({ scope, field: event.target.value })}
            placeholder="image_url" />
          <datalist id={urlFieldListId}>
            {[...new Set(["image_url", "video_url", "audio_url", ...fields])].map((name) => <option key={name} value={name} />)}
          </datalist>
        </label>
      </div>
      <div className="fal-studio-action-row">
        <button type="button" className="fal-studio-button-secondary"
          disabled={disabled || !file || uploading} onClick={upload}>
          {uploading ? "Sending to Fal…" : "Upload reference ↗"}
        </button>
        {uploading && <button type="button" className="fal-studio-button-outline"
          onClick={() => xhrRef.current?.abort()}>Stop upload</button>}
      </div>
      {uploading && <div className="fal-studio-upload-progress" role="progressbar"
        aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={progress ?? undefined} aria-label="Media upload progress">
        <div style={{width: `${progress ?? 4}%`}} />
      </div>}
      {!!message && <p className="fal-studio-inline-warning" role="status">{message}</p>}
      <small className="fal-studio-uploader-fineprint">
        Maximum 512 MiB. Requires the optional <code>fal-client</code> package for CDN upload.
        Some models require specific formats or file sizes.
      </small>
    </div>
  );
}
