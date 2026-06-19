const form = document.querySelector("#verify-form");
const submitButton = document.querySelector("#submit-button");
const loadingMessage = document.querySelector("#loading-message");
const errorBox = document.querySelector("#error-box");
const results = document.querySelector("#results");
const statusLine = document.querySelector("#status-line");
const modeEyebrow = document.querySelector("#mode-eyebrow");
const imageInput = document.querySelector("#image");
const selectedFile = document.querySelector("#selected-file");
const imagePreview = document.querySelector("#image-preview");

const singleModeButton = document.querySelector("#single-mode-button");
const batchModeButton = document.querySelector("#batch-mode-button");
const batchForm = document.querySelector("#batch-form");
const batchImageInput = document.querySelector("#batch-images");
const batchSelectedFiles = document.querySelector("#batch-selected-files");
const batchList = document.querySelector("#batch-list");
const batchSubmitButton = document.querySelector("#batch-submit-button");
const batchLoadingMessage = document.querySelector("#batch-loading-message");
const batchProgressPanel = document.querySelector("#batch-progress-panel");
const batchErrorBox = document.querySelector("#batch-error-box");
const batchResults = document.querySelector("#batch-results");

const VERIFY_TIMEOUT_MS = 30000;
const PROGRESS_DELAY_MS = 600;
const MAX_BATCH_LABELS = 10;
const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
const ALLOWED_IMAGE_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);

const FIELDS = [
  { name: "brand_name", label: "Brand Name", type: "input" },
  { name: "class_type", label: "Class / Type", type: "input" },
  { name: "producer_name", label: "Producer Name", type: "input" },
  { name: "country_of_origin", label: "Country of Origin", type: "input" },
  { name: "alcohol_by_volume", label: "Alcohol by Volume", type: "input" },
  { name: "net_contents", label: "Net Contents", type: "input" },
  { name: "government_warning", label: "Government Warning", type: "textarea" },
];

const FIELD_LABELS = Object.fromEntries(FIELDS.map((field) => [field.name, field.label]));
let previewUrl = null;
let batchItems = [];

singleModeButton.addEventListener("click", () => setMode("single"));
batchModeButton.addEventListener("click", () => setMode("batch"));

imageInput.addEventListener("change", () => {
  const file = imageInput.files?.[0];
  setSelectedFile(file);
});

batchImageInput.addEventListener("change", () => {
  clearBatchPreviewUrls();
  batchItems = Array.from(batchImageInput.files || []).map((file, index) => ({
    id: makeClientId(index),
    file,
    previewUrl: ALLOWED_IMAGE_TYPES.has(file.type) ? URL.createObjectURL(file) : null,
  }));
  hideError(batchErrorBox);
  batchResults.hidden = true;
  renderBatchRows();
  statusLine.textContent = batchItems.length
    ? `Ready to check ${batchItems.length} labels.`
    : "Ready to check a batch.";
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  hideError(errorBox);
  results.hidden = true;

  const validationErrors = validateSingleForm();
  if (validationErrors.length > 0) {
    showError(errorBox, "Please complete these items.", validationErrors);
    return;
  }

  const timeout = new AbortController();
  const timeoutId = window.setTimeout(() => timeout.abort(), VERIFY_TIMEOUT_MS);

  setSingleBusy(true);
  statusLine.textContent = "Checking the label.";

  try {
    const response = await fetch("/verify", {
      method: "POST",
      body: new FormData(form),
      signal: timeout.signal,
    });
    const data = await readJson(response);

    if (!response.ok) {
      const readableError = readableApiError(data);
      showError(errorBox, readableError.message, readableError.details);
      statusLine.textContent = "The label could not be checked.";
      return;
    }

    renderSingleResults(data);
    statusLine.textContent = "Label check complete.";
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      showError(errorBox, "Verification took too long. Please try again.");
    } else {
      showError(errorBox, "Unable to reach verification right now. Please try again.");
    }
    statusLine.textContent = "The label could not be checked.";
  } finally {
    window.clearTimeout(timeoutId);
    setSingleBusy(false);
  }
});

batchForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  hideError(batchErrorBox);
  batchResults.hidden = true;

  const validationErrors = validateBatchForm();
  if (validationErrors.length > 0) {
    showError(batchErrorBox, "Please complete these items.", validationErrors);
    return;
  }

  const formData = new FormData();
  const itemPayload = batchItems.map((item) => {
    formData.append("images", item.file, item.file.name);
    return {
      client_id: item.id,
      ...batchItemFieldValues(item.id),
    };
  });
  formData.append("items", JSON.stringify(itemPayload));

  const timeout = new AbortController();
  const timeoutId = window.setTimeout(() => timeout.abort(), VERIFY_TIMEOUT_MS);
  const progressId = window.setTimeout(() => {
    batchProgressPanel.hidden = false;
  }, PROGRESS_DELAY_MS);

  setBatchBusy(true);
  statusLine.textContent = "Checking labels...";

  try {
    const response = await fetch("/verify/batch", {
      method: "POST",
      body: formData,
      signal: timeout.signal,
    });
    const data = await readJson(response);

    if (!response.ok) {
      const readableError = readableApiError(data);
      showError(batchErrorBox, readableError.message, readableError.details);
      statusLine.textContent = "The batch could not be checked.";
      return;
    }

    renderBatchResults(data);
    statusLine.textContent = "Batch check complete.";
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      showError(batchErrorBox, "Batch verification took too long. Please try again.");
    } else {
      showError(batchErrorBox, "Unable to reach verification right now. Please try again.");
    }
    statusLine.textContent = "The batch could not be checked.";
  } finally {
    window.clearTimeout(timeoutId);
    window.clearTimeout(progressId);
    setBatchBusy(false);
  }
});

function setMode(mode) {
  const isBatch = mode === "batch";
  singleModeButton.classList.toggle("active", !isBatch);
  batchModeButton.classList.toggle("active", isBatch);
  singleModeButton.setAttribute("aria-pressed", String(!isBatch));
  batchModeButton.setAttribute("aria-pressed", String(isBatch));
  form.hidden = isBatch;
  results.hidden = isBatch || results.childElementCount === 0;
  batchForm.hidden = !isBatch;
  batchResults.hidden = !isBatch || batchResults.childElementCount === 0;
  modeEyebrow.textContent = isBatch ? "Batch Label Check" : "Single Label Check";
  statusLine.textContent = isBatch ? "Ready to check a batch." : "Ready to check one label.";
}

function setSelectedFile(file) {
  if (previewUrl !== null) {
    URL.revokeObjectURL(previewUrl);
    previewUrl = null;
  }

  if (!file) {
    selectedFile.textContent = "No image selected";
    imagePreview.hidden = true;
    imagePreview.removeAttribute("src");
    return;
  }

  selectedFile.textContent = file.name;

  if (ALLOWED_IMAGE_TYPES.has(file.type)) {
    previewUrl = URL.createObjectURL(file);
    imagePreview.src = previewUrl;
    imagePreview.hidden = false;
  } else {
    imagePreview.hidden = true;
    imagePreview.removeAttribute("src");
  }
}

function renderBatchRows() {
  batchList.replaceChildren();
  batchSelectedFiles.textContent = batchItems.length
    ? `${batchItems.length} image${batchItems.length === 1 ? "" : "s"} selected`
    : "No images selected";

  for (const item of batchItems) {
    batchList.append(renderBatchRow(item));
  }
}

function renderBatchRow(item) {
  const row = document.createElement("article");
  row.className = "batch-label-card";
  row.dataset.clientId = item.id;

  const header = document.createElement("div");
  header.className = "batch-label-header";

  const preview = document.createElement("div");
  preview.className = "batch-preview";
  if (item.previewUrl) {
    const image = document.createElement("img");
    image.src = item.previewUrl;
    image.alt = `${item.file.name} preview`;
    preview.append(image);
  } else {
    preview.textContent = "No preview";
  }

  const title = document.createElement("h2");
  title.textContent = item.file.name;

  const removeButton = document.createElement("button");
  removeButton.type = "button";
  removeButton.className = "remove-button";
  removeButton.textContent = "Remove";
  removeButton.setAttribute("aria-label", `Remove ${item.file.name} from the batch`);
  removeButton.addEventListener("click", () => removeBatchItem(item.id));

  header.append(preview, title, removeButton);
  row.append(header, renderBatchFields(item.id));
  return row;
}

function renderBatchFields(clientId) {
  const grid = document.createElement("div");
  grid.className = "fields-grid";

  for (const field of FIELDS) {
    const wrapper = document.createElement("div");
    wrapper.className = field.name === "government_warning" ? "field field-wide" : "field";

    const inputId = `${clientId}-${field.name}`;
    const label = document.createElement("label");
    label.setAttribute("for", inputId);
    label.textContent = field.label;

    const input =
      field.type === "textarea" ? document.createElement("textarea") : document.createElement("input");
    input.id = inputId;
    input.dataset.field = field.name;
    input.required = true;
    if (field.type === "textarea") {
      input.rows = 6;
    } else {
      input.type = "text";
      input.autocomplete = "off";
    }

    wrapper.append(label, input);
    grid.append(wrapper);
  }

  return grid;
}

function removeBatchItem(clientId) {
  const item = batchItems.find((candidate) => candidate.id === clientId);
  if (item?.previewUrl) {
    URL.revokeObjectURL(item.previewUrl);
  }
  batchItems = batchItems.filter((candidate) => candidate.id !== clientId);
  renderBatchRows();
  statusLine.textContent = batchItems.length
    ? `Ready to check ${batchItems.length} labels.`
    : "Ready to check a batch.";
}

function validateSingleForm() {
  const errors = [];
  const file = imageInput.files?.[0];

  if (!file) {
    errors.push("Label Image: Please choose a JPG, PNG, or WebP image.");
  } else if (!ALLOWED_IMAGE_TYPES.has(file.type)) {
    errors.push("Label Image: Please choose a JPG, PNG, or WebP image.");
  }

  for (const field of FIELDS) {
    const input = form.elements[field.name];
    if (!input.value.trim()) {
      errors.push(`${field.label}: Field is required.`);
    }
  }

  return errors;
}

function validateBatchForm() {
  const errors = [];

  if (batchItems.length === 0) {
    errors.push("Label Images: Please choose at least one JPG, PNG, or WebP image.");
  }
  if (batchItems.length > MAX_BATCH_LABELS) {
    errors.push(`Label Images: Please choose no more than ${MAX_BATCH_LABELS} images.`);
  }

  for (const item of batchItems) {
    if (!ALLOWED_IMAGE_TYPES.has(item.file.type)) {
      errors.push(`${item.file.name}: Please choose a JPG, PNG, or WebP image.`);
    }
    if (item.file.size > MAX_IMAGE_BYTES) {
      errors.push(`${item.file.name}: Please choose an image smaller than 10 MB.`);
    }

    for (const field of FIELDS) {
      const value = batchFieldInput(item.id, field.name)?.value || "";
      if (!value.trim()) {
        errors.push(`${item.file.name} - ${field.label}: Field is required.`);
      }
    }
  }

  return errors;
}

function batchItemFieldValues(clientId) {
  return Object.fromEntries(
    FIELDS.map((field) => [field.name, batchFieldInput(clientId, field.name).value.trim()]),
  );
}

function batchFieldInput(clientId, fieldName) {
  return batchList.querySelector(`[data-client-id="${clientId}"] [data-field="${fieldName}"]`);
}

function setSingleBusy(isBusy) {
  submitButton.disabled = isBusy;
  submitButton.textContent = isBusy ? "Checking Label..." : "Verify Label";
  loadingMessage.hidden = !isBusy;

  for (const element of form.elements) {
    if (element !== submitButton) {
      element.disabled = isBusy;
    }
  }
}

function setBatchBusy(isBusy) {
  batchSubmitButton.disabled = isBusy;
  batchSubmitButton.textContent = isBusy ? "Checking Labels..." : "Verify Batch";
  batchLoadingMessage.hidden = !isBusy;
  if (!isBusy) {
    batchProgressPanel.hidden = true;
  }

  for (const element of batchForm.elements) {
    if (element !== batchSubmitButton) {
      element.disabled = isBusy;
    }
  }
}

async function readJson(response) {
  const text = await response.text();
  if (!text) {
    return null;
  }

  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function readableApiError(data) {
  const error = data?.error;
  return {
    message: cleanErrorMessage(error?.message),
    details: Array.isArray(error?.details) ? error.details.map(cleanErrorDetail) : [],
  };
}

function cleanErrorMessage(message) {
  if (typeof message !== "string" || message.trim() === "") {
    return "Verification could not be completed.";
  }

  return message;
}

function cleanErrorDetail(detail) {
  if (typeof detail !== "string") {
    return "Please check the form and try again.";
  }

  const [rawField, ...rest] = detail.split(":");
  const field = rawField.trim();
  const message = rest.join(":").trim();
  const label = FIELD_LABELS[field] || (field === "image" || field === "images" ? "Label Image" : field);
  const readableMessage = message
    .replace(/^Field required\.?$/i, "Field is required.")
    .replace(/^Input should be a valid string\.?$/i, "Please enter text.");

  return readableMessage ? `${label}: ${readableMessage}` : label;
}

function showError(target, message, details = []) {
  target.replaceChildren();

  const headline = document.createElement("p");
  headline.textContent = message;
  target.append(headline);

  if (details.length > 0) {
    const list = document.createElement("ul");
    for (const detail of details) {
      const item = document.createElement("li");
      item.textContent = detail;
      list.append(item);
    }
    target.append(list);
  }

  target.hidden = false;
  target.scrollIntoView({ behavior: "smooth", block: "center" });
  target.focus({ preventScroll: true });
}

function hideError(target) {
  target.hidden = true;
  target.replaceChildren();
}

function renderSingleResults(data) {
  results.replaceChildren();

  const verdict = data?.verdict === "PASS" ? "APPROVED" : "NEEDS REVIEW";
  const verdictClass = data?.verdict === "PASS" ? "approved" : "review";
  const verdictPanel = document.createElement("div");
  verdictPanel.className = `verdict-panel ${verdictClass}`;

  const verdictTitle = document.createElement("h2");
  verdictTitle.className = "verdict-title";
  verdictTitle.textContent = verdict;

  const elapsedTime = document.createElement("p");
  elapsedTime.className = "elapsed-time";
  elapsedTime.textContent = `Checked in ${formatLatency(data?.latency_ms)}`;

  verdictPanel.append(verdictTitle, elapsedTime);
  results.append(verdictPanel);

  const fieldResults = document.createElement("div");
  fieldResults.className = "field-results";

  for (const field of orderedFieldResults(data?.fields)) {
    fieldResults.append(renderFieldResult(field));
  }

  const actions = document.createElement("div");
  actions.className = "secondary-actions";
  actions.append(
    secondaryButton("Check Another Label", resetSinglePage),
    secondaryButton("Edit and Check Again", focusSingleForm),
  );

  results.append(fieldResults, actions);
  results.hidden = false;
  results.scrollIntoView({ behavior: "smooth", block: "start" });
  results.focus({ preventScroll: true });
}

function renderBatchResults(data) {
  batchResults.replaceChildren();

  const summary = data?.summary || { passed: 0, needs_review: 0, total: 0, latency_ms: null };
  const summaryBand = document.createElement("div");
  summaryBand.className = "batch-summary";
  summaryBand.append(
    summaryMetric("Passed", summary.passed),
    summaryMetric("Needs Review", summary.needs_review),
    summaryMetric("Total", summary.total),
  );

  const elapsedTime = document.createElement("p");
  elapsedTime.className = "elapsed-time";
  elapsedTime.textContent = `Checked in ${formatLatency(summary.latency_ms)}`;

  const resultList = document.createElement("div");
  resultList.className = "batch-result-list";
  for (const item of Array.isArray(data?.items) ? data.items : []) {
    resultList.append(renderBatchResultItem(item));
  }

  const actions = document.createElement("div");
  actions.className = "secondary-actions";
  actions.append(
    secondaryButton("Check Another Batch", resetBatchPage),
    secondaryButton("Edit and Check Again", focusBatchForm),
  );

  batchResults.append(summaryBand, elapsedTime, resultList, actions);
  batchResults.hidden = false;
  batchResults.scrollIntoView({ behavior: "smooth", block: "start" });
  batchResults.focus({ preventScroll: true });
}

function summaryMetric(label, value) {
  const metric = document.createElement("div");
  metric.className = "summary-metric";

  const number = document.createElement("strong");
  number.textContent = String(value ?? 0);

  const text = document.createElement("span");
  text.textContent = label;

  metric.append(number, text);
  return metric;
}

function renderBatchResultItem(item) {
  const details = document.createElement("details");
  details.className = `batch-result-item ${item?.verdict === "PASS" ? "pass" : "review"}`;

  const summary = document.createElement("summary");
  const title = document.createElement("span");
  title.textContent = item?.filename || item?.client_id || "Label";

  const status = document.createElement("strong");
  status.textContent = item?.error ? "Could Not Check" : item?.verdict === "PASS" ? "Passed" : "Needs Review";

  summary.append(title, status);
  details.append(summary);

  if (item?.error) {
    const error = document.createElement("p");
    error.className = "item-error";
    error.textContent = item.error;
    details.append(error);
    return details;
  }

  const fieldResults = document.createElement("div");
  fieldResults.className = "field-results compact";
  for (const field of orderedFieldResults(item?.fields)) {
    fieldResults.append(renderFieldResult(field));
  }
  details.append(fieldResults);
  return details;
}

function orderedFieldResults(fields) {
  const byName = new Map(Array.isArray(fields) ? fields.map((field) => [field.field, field]) : []);
  return FIELDS.map((field) => byName.get(field.name)).filter(Boolean);
}

function renderFieldResult(field) {
  const passed = field.status === "PASS";
  const row = document.createElement("article");
  row.className = `field-result ${passed ? "pass" : "fail"}`;

  const content = document.createElement("div");

  const name = document.createElement("h3");
  name.className = "field-name";
  name.textContent = FIELD_LABELS[field.field] || field.field;
  content.append(name);

  if (passed) {
    const note = document.createElement("p");
    note.className = "match-note";
    note.textContent = "Matched.";
    content.append(note);
  } else {
    content.append(
      resultDetail("Expected", formatValue(field.application_value, "expected")),
      resultDetail("Found", formatValue(field.extracted_value, "found")),
      resultMessage(readableFieldFailure(field)),
    );
  }

  const badge = document.createElement("div");
  badge.className = `badge ${passed ? "pass" : "fail"}`;
  badge.textContent = passed ? "PASS" : "FAIL";

  row.append(content, badge);
  return row;
}

function resultDetail(label, value) {
  const detail = document.createElement("p");
  detail.className = "result-detail";
  detail.textContent = `${label}: ${value}`;
  return detail;
}

function resultMessage(message) {
  const detail = document.createElement("p");
  detail.className = "result-message";
  detail.textContent = message;
  return detail;
}

function formatValue(value, kind) {
  if (value === null || value === undefined || String(value).trim() === "") {
    return kind === "found" ? "Could not read this on the label." : "Not provided.";
  }

  return String(value);
}

function readableFieldFailure(field) {
  if (field.extracted_value === null || field.extracted_value === undefined || String(field.extracted_value).trim() === "") {
    return "This field could not be read clearly on the label.";
  }

  const messages = {
    brand_name: "The brand name on the label does not match the application.",
    class_type: "The class or type on the label does not match the application.",
    producer_name: "The producer name on the label does not match the application.",
    country_of_origin: "The country on the label does not match the application.",
    alcohol_by_volume: "The alcohol percentage is different.",
    net_contents: "The bottle size is different.",
    government_warning: "Exact match required, including capital letters and punctuation.",
  };

  return messages[field.field] || "This field does not match the application.";
}

function formatLatency(value) {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "a few seconds";
  }

  return `${Math.round(value)} ms`;
}

function secondaryButton(text, onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "secondary-button";
  button.textContent = text;
  button.addEventListener("click", onClick);
  return button;
}

function resetSinglePage() {
  form.reset();
  setSelectedFile(null);
  hideError(errorBox);
  results.hidden = true;
  results.replaceChildren();
  statusLine.textContent = "Ready to check one label.";
  imageInput.focus();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function focusSingleForm() {
  hideError(errorBox);
  results.hidden = true;
  results.replaceChildren();
  statusLine.textContent = "Ready to check one label.";
  form.scrollIntoView({ behavior: "smooth", block: "start" });
  imageInput.focus();
}

function resetBatchPage() {
  batchForm.reset();
  clearBatchPreviewUrls();
  batchItems = [];
  renderBatchRows();
  hideError(batchErrorBox);
  batchResults.hidden = true;
  batchResults.replaceChildren();
  statusLine.textContent = "Ready to check a batch.";
  batchImageInput.focus();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function focusBatchForm() {
  hideError(batchErrorBox);
  batchResults.hidden = true;
  batchResults.replaceChildren();
  statusLine.textContent = batchItems.length
    ? `Ready to check ${batchItems.length} labels.`
    : "Ready to check a batch.";
  batchForm.scrollIntoView({ behavior: "smooth", block: "start" });
  batchImageInput.focus();
}

function clearBatchPreviewUrls() {
  for (const item of batchItems) {
    if (item.previewUrl) {
      URL.revokeObjectURL(item.previewUrl);
    }
  }
}

function makeClientId(index) {
  if (window.crypto?.randomUUID) {
    return window.crypto.randomUUID();
  }
  return `label-${Date.now()}-${index}`;
}
