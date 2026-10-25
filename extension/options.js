const core = document.getElementById("core");
const token = document.getElementById("token");
const saved = document.getElementById("saved");

chrome.storage.local.get(["core", "token"]).then((v) => {
  core.value = v.core || "http://127.0.0.1:9234";
  token.value = v.token || "";
});

document.getElementById("save").addEventListener("click", async () => {
  await chrome.storage.local.set({ core: core.value.trim(), token: token.value.trim() });
  saved.textContent = "saved";
  setTimeout(() => (saved.textContent = ""), 1500);
});
