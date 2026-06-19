const form = document.querySelector("#verify-form");
const submitButton = document.querySelector("#submit-button");
const loadingMessage = document.querySelector("#loading-message");
const errorBox = document.querySelector("#error-box");
const results = document.querySelector("#results");
const statusLine = document.querySelector("#status-line");
const imageInput = document.querySelector("#image");
const selectedFile = document.querySelector("#selected-file");
const imagePreview = document.querySelector("#image-preview");

const VERIFY_TIMEOUT_MS = 30000;
const ALLOWED_IMAGE_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);

const FIELDS = [
  { name: "brand_name", label: "Brand Name" },
  { name: "class_type", label: "Class / Type" },
  { name: "producer_name", label: "Producer Name" },
  { name: "country_of_origin", label: "Country of Origin" },
  { name: "alcohol_by_volume", label: "Alcohol by Volume" },
  { name: "net_contents", label: "Net Contents" },
  { name: "government_warning", label: "Government Warning" },
];

const FIELD_LABELS = Object.fromEntries(FIELDS.map((field) => [field.name, field.label]));
let previewUrl = null;

imageInput.addEventListener("change", () => {
  const file = imageInput.files?.[0];
  setSelectedFile(file);
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  hideError();
  results.hidden = true;

  const validationErrors = validateForm();
  if (validationErrors.length > 0) {
    showError("Please complete these items.", validationErrors);
    return;
  }

  const timeout = new AbortController();
  const timeoutId = window.setTimeout(() => timeout.abort(), VERIFY_TIMEOUT_MS);

  setBusy(true);
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
      showError(readableError.message, readableError.details);
      statusLine.textContent = "The label could not be checked.";
      return;
    }

    renderResults(data);
    statusLine.textContent = "Label check complete.";
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      showError("Verification took too long. Please try again.");
    } else {
      showError("Unable to reach verification right now. Please try again.");
    }
    statusLine.textContent = "The label could not be checked.";
  } finally {
    window.clearTimeout(timeoutId);
    setBusy(false);
  }
});

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

function validateForm() {
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

function setBusy(isBusy) {
  submitButton.disabled = isBusy;
  submitButton.textContent = isBusy ? "Checking Label..." : "Verify Label";
  loadingMessage.hidden = !isBusy;

  for (const element of form.elements) {
    if (element !== submitButton) {
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
  const label = FIELD_LABELS[field] || (field === "image" ? "Label Image" : field);
  const readableMessage = message
    .replace(/^Field required\.?$/i, "Field is required.")
    .replace(/^Input should be a valid string\.?$/i, "Please enter text.");

  return readableMessage ? `${label}: ${readableMessage}` : label;
}

function showError(message, details = []) {
  errorBox.replaceChildren();

  const headline = document.createElement("p");
  headline.textContent = message;
  errorBox.append(headline);

  if (details.length > 0) {
    const list = document.createElement("ul");
    for (const detail of details) {
      const item = document.createElement("li");
      item.textContent = detail;
      list.append(item);
    }
    errorBox.append(list);
  }

  errorBox.hidden = false;
  errorBox.scrollIntoView({ behavior: "smooth", block: "center" });
}

function hideError() {
  errorBox.hidden = true;
  errorBox.replaceChildren();
}

function renderResults(data) {
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
    secondaryButton("Check Another Label", resetPage),
    secondaryButton("Edit and Check Again", focusForm),
  );

  results.append(fieldResults, actions);
  results.hidden = false;
  results.scrollIntoView({ behavior: "smooth", block: "start" });
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

function resetPage() {
  form.reset();
  setSelectedFile(null);
  hideError();
  results.hidden = true;
  results.replaceChildren();
  statusLine.textContent = "Ready to check one label.";
  imageInput.focus();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function focusForm() {
  hideError();
  results.hidden = true;
  results.replaceChildren();
  statusLine.textContent = "Ready to check one label.";
  form.scrollIntoView({ behavior: "smooth", block: "start" });
  imageInput.focus();
}
