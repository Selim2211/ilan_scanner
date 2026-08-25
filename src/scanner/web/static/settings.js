/* Ayarlar ekraninda JS iki is yapar: "Veriyi kontrol et" ve yeni kaynak formunun
   tur/alan gosterimi. Formlarin kaydedilmesi duz POST - sayfa yenilenince yazilan
   metin kaybolmasin diye sadece kontrol JS ile yapiliyor. */

/* --- yeni kaynak formu: RSS mi JSON mu --- */
function syncKind() {
  const select = document.getElementById("c-kind");
  if (!select) return;
  const json = select.value === "json";
  document.querySelectorAll(".json-only").forEach((el) => { el.hidden = !json; });
  document.querySelectorAll(".rss-only").forEach((el) => { el.hidden = json; });
}
document.addEventListener("change", (event) => {
  if (event.target.id === "c-kind") syncKind();
});
document.addEventListener("DOMContentLoaded", syncKind);

function customDefinition() {
  const form = document.getElementById("custom-form");
  const value = (name) => (form.elements[name] ? form.elements[name].value.trim() : "");
  const fields = {};
  form.querySelectorAll('[name^="field__"]').forEach((input) => {
    if (input.value.trim()) fields[input.name.replace("field__", "")] = input.value.trim();
  });
  return {
    title: value("title"),
    name: value("title"),
    kind: value("kind"),
    url: value("url"),
    items_path: value("items_path"),
    pages: value("pages"),
    site_url: value("site_url"),
    queries: value("queries").split("\n").map((s) => s.trim()).filter(Boolean),
    fields,
  };
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest(".probe-btn, #custom-test");
  if (!button) return;

  const testing = button.id === "custom-test";
  const name = testing ? "custom" : button.dataset.source;
  const box = document.getElementById("probe-" + name);
  button.disabled = true;
  box.hidden = false;
  box.className = "probe-result busy";
  box.textContent = "Kontrol ediliyor…";

  try {
    const body = testing
      ? { definition: customDefinition() }
      : { source: name };
    const response = await fetch("/api/kaynak-kontrol", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json();
    box.className = "probe-result " + (data.ok ? "ok" : "err");

    const head = document.createElement("div");
    head.className = "probe-line";
    head.textContent = data.message + (data.elapsed_ms ? ` · ${data.elapsed_ms} ms` : "");
    box.replaceChildren(head);

    if (data.query) {
      const q = document.createElement("div");
      q.className = "probe-meta";
      q.textContent = `sorgu: ${data.query}`;
      box.append(q);
    }
    if (data.titles && data.titles.length) {
      const list = document.createElement("ul");
      data.titles.forEach((title) => {
        const item = document.createElement("li");
        item.textContent = title;
        list.append(item);
      });
      box.append(list);
    }
    [data.warning, data.detail].forEach((text) => {
      if (!text) return;
      const line = document.createElement("div");
      line.className = "probe-meta";
      line.textContent = text;
      box.append(line);
    });
  } catch (err) {
    box.className = "probe-result err";
    box.textContent = "Kontrol isteği gönderilemedi: " + err;
  } finally {
    button.disabled = false;
  }
});
