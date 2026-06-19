const result = document.querySelector("#health-result");
const indicator = document.querySelector("#health-indicator");

async function checkHealth() {
  try {
    const response = await fetch("/health", { cache: "no-store" });

    if (!response.ok) {
      throw new Error(`Health check failed with HTTP ${response.status}`);
    }

    const data = await response.json();
    result.textContent = JSON.stringify(data, null, 2);
    indicator.className = "indicator healthy";
  } catch (error) {
    result.textContent = error instanceof Error ? error.message : "Unable to reach backend.";
    indicator.className = "indicator unhealthy";
  }
}

checkHealth();
