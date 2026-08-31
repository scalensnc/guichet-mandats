"use strict";

const state = { projects: [], cadastrations: [], filtered: [], visible: 30, map: null, clusters: null, markers: new Map(), selected: null };
const elements = {
  search: document.querySelector("#searchInput"),
  layer: document.querySelector("#layerFilter"),
  commune: document.querySelector("#communeFilter"),
  year: document.querySelector("#yearFilter"),
  location: document.querySelector("#locationFilter"),
  reset: document.querySelector("#resetFilters"),
  list: document.querySelector("#projectList"),
  count: document.querySelector("#resultCount"),
  loadMore: document.querySelector("#loadMore"),
  template: document.querySelector("#projectTemplate"),
  dialog: document.querySelector("#projectDialog"),
};

const collator = new Intl.Collator("fr", { sensitivity: "base", numeric: true });
const dateFormatter = new Intl.DateTimeFormat("fr-CH", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });
const numberFormatter = new Intl.NumberFormat("fr-CH");

function formatDate(value) {
  if (!value) return "Non renseignée";
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isNaN(date.valueOf()) ? value : dateFormatter.format(date);
}

function normalize(value) {
  return String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("fr");
}

function activeProjects() {
  return elements.layer.value === "cadastrations" ? state.cadastrations : state.projects;
}

function displayDate(project) {
  return project.received_date || project.date;
}

function initMap() {
  state.map = L.map("map", { zoomControl: false }).setView([46.65, 6.62], 9);
  L.control.zoom({ position: "topright" }).addTo(state.map);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(state.map);
  state.clusters = L.markerClusterGroup({ showCoverageOnHover: false, maxClusterRadius: 48 });
  state.map.addLayer(state.clusters);

  // Leaflet mémorise les dimensions du conteneur. GitHub Pages et les
  // changements de breakpoint peuvent stabiliser la grille après cette
  // initialisation : on recalcule alors la surface utile de la carte.
  const mapElement = document.querySelector("#map");
  const resizeObserver = new ResizeObserver(() => {
    requestAnimationFrame(() => state.map?.invalidateSize({ animate: false }));
  });
  resizeObserver.observe(mapElement);
  window.addEventListener("load", () => state.map.invalidateSize({ animate: false }), { once: true });
}

function popupContent(project) {
  const wrapper = document.createElement("div");
  const dossierNumber = document.createElement("p");
  const title = document.createElement("p");
  dossierNumber.className = "popup-number";
  title.className = "popup-title";
  const dossierId = String(project.id || "—");
  dossierNumber.textContent = dossierId.replace(/^0(?=.)/, "");
  title.textContent = project.name || "Dossier sans intitulé";
  wrapper.append(dossierNumber, title);
  if (project.kind === "cadastration") {
    const remark = document.createElement("p");
    remark.className = "popup-remark";
    remark.textContent = `Remarque : ${project.remark || "—"}`;
    wrapper.append(remark);
  }
  return wrapper;
}

function rebuildMarkers() {
  state.clusters.clearLayers();
  state.markers.clear();
  const isCadastration = elements.layer.value === "cadastrations";
  const markers = [];
  for (const project of state.filtered) {
    if (!Number.isFinite(project.lat) || !Number.isFinite(project.lon)) continue;
    const statusClass = String(project.status || "cadastration_en_attente").replaceAll("_", "-");
    const icon = L.divIcon({
      className: isCadastration ? `cadastration-marker status-${statusClass}` : "mandate-marker",
      iconSize: isCadastration ? [19, 19] : [17, 17],
    });
    const marker = L.marker([project.lat, project.lon], { icon, title: project.name || project.id || "Mandat" });
    marker.bindPopup(() => popupContent(project));
    marker.on("click", () => { state.selected = project; });
    state.markers.set(project.id, marker);
    markers.push(marker);
  }
  state.clusters.addLayers(markers);
}

function renderList() {
  elements.list.replaceChildren();
  const fragment = document.createDocumentFragment();
  for (const project of state.filtered.slice(0, state.visible)) {
    const card = elements.template.content.firstElementChild.cloneNode(true);
    const isCadastration = project.kind === "cadastration";
    card.classList.toggle("is-cadastration", isCadastration);
    card.querySelector(".project-number").textContent = project.id ? `N° ${project.id}` : "Sans numéro";
    card.querySelector(".project-date").textContent = formatDate(displayDate(project));
    card.querySelector(".project-name").textContent = project.name || "Dossier sans intitulé";
    card.querySelector(".project-kind").hidden = !isCadastration;
    card.querySelector(".project-commune").textContent = project.commune || "Commune inconnue";
    card.querySelector(".project-parcel").textContent = project.parcel ? `BF ${project.parcel}` : "Sans parcelle";
    card.addEventListener("click", () => openDetails(project));
    fragment.append(card);
  }
  if (!state.filtered.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "Aucun dossier ne correspond à ces critères. Essayez une recherche plus large.";
    fragment.append(empty);
  }
  elements.list.append(fragment);
  elements.list.setAttribute("aria-busy", "false");
  elements.count.textContent = `${numberFormatter.format(state.filtered.length)} dossier${state.filtered.length > 1 ? "s" : ""}`;
  elements.loadMore.hidden = state.visible >= state.filtered.length;
}

function applyFilters() {
  const query = normalize(elements.search.value.trim());
  const commune = elements.commune.value;
  const year = elements.year.value;
  const location = elements.location.value;
  state.filtered = activeProjects().filter((project) => {
    const haystack = normalize([project.id, project.name, project.commune, project.parcel, project.details].join(" "));
    const isMapped = Number.isFinite(project.lat) && Number.isFinite(project.lon);
    return (!query || haystack.includes(query))
      && (!commune || project.commune === commune)
      && (!year || String(displayDate(project) || "").startsWith(year))
      && (!location || (location === "mapped" ? isMapped : !isMapped));
  });
  state.visible = 30;
  renderList();
  rebuildMarkers();
}

function openDetails(project) {
  state.selected = project;
  document.querySelector("#detailId").textContent = project.id || "—";
  document.querySelector("#detailName").textContent = project.name || "Dossier sans intitulé";
  document.querySelector("#detailCommune").textContent = project.commune || "Non renseignée";
  document.querySelector("#detailParcel").textContent = project.parcel ? `BF ${project.parcel}` : "Non renseignée";
  document.querySelector("#detailDate").textContent = formatDate(project.date);
  document.querySelector("#detailLocation").textContent = Number.isFinite(project.lat) ? "Disponible sur la carte" : "Coordonnées indisponibles";
  document.querySelector("#showOnMap").hidden = !Number.isFinite(project.lat);
  const optionalDetails = [
    ["detailReceptionRow", "detailReception", project.received_date && formatDate(project.received_date)],
    ["detailMeasurementRow", "detailMeasurement", project.measurement],
    ["detailDetailsRow", "detailDetails", project.details],
  ];
  for (const [rowId, valueId, value] of optionalDetails) {
    document.querySelector(`#${rowId}`).hidden = !value;
    document.querySelector(`#${valueId}`).textContent = value || "";
  }
  elements.dialog.showModal();
}

function showSelectedOnMap() {
  const project = state.selected;
  const marker = project && state.markers.get(project.id);
  if (!marker) return;
  elements.dialog.close();
  state.map.setView(marker.getLatLng(), 16, { animate: true });
  state.clusters.zoomToShowLayer(marker, () => marker.openPopup());
  if (window.innerWidth <= 900) document.querySelector(".map-panel").scrollIntoView({ behavior: "smooth" });
}

function populateFilters() {
  const projects = activeProjects();
  const communes = [...new Set(projects.map((p) => p.commune).filter(Boolean))].sort(collator.compare);
  const years = [...new Set(projects.map((p) => displayDate(p)?.slice(0, 4)).filter(Boolean))].sort().reverse();
  elements.commune.options.length = 1;
  elements.year.options.length = 1;
  for (const commune of communes) elements.commune.add(new Option(commune, commune));
  for (const year of years) elements.year.add(new Option(year, year));
}

function renderStats(payload, cadastrationPayload) {
  const mapped = state.projects.filter((p) => Number.isFinite(p.lat) && Number.isFinite(p.lon)).length;
  const communes = new Set(state.projects.map((p) => p.commune).filter(Boolean)).size;
  document.querySelector("#totalStat").textContent = numberFormatter.format(state.projects.length);
  document.querySelector("#mappedStat").textContent = numberFormatter.format(mapped);
  document.querySelector("#communesStat").textContent = numberFormatter.format(communes);
  document.querySelector("#cadastrationStat").textContent = numberFormatter.format(state.cadastrations.length);
  document.querySelector("#updatedAt").textContent = payload.source_updated_at
    ? `Bexio au ${formatDate(payload.source_updated_at)} · cadastrations au ${formatDate(cadastrationPayload.source_updated_at)}`
    : "Date de mise à jour inconnue";
}

async function start() {
  initMap();
  try {
    const [response, cadastrationResponse] = await Promise.all([
      fetch("data/projects.json", { cache: "no-store" }),
      fetch("data/cadastrations.json", { cache: "no-store" }),
    ]);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    const cadastrationPayload = cadastrationResponse.ok ? await cadastrationResponse.json() : {};
    state.projects = (Array.isArray(payload.projects) ? payload.projects : []).map((project) => ({ ...project, kind: "project" }));
    state.cadastrations = (Array.isArray(cadastrationPayload.cadastrations) ? cadastrationPayload.cadastrations : []).map((project) => ({ ...project, kind: "cadastration" }));
    state.filtered = activeProjects();
    populateFilters();
    renderStats(payload, cadastrationPayload);
    renderList();
    rebuildMarkers();
  } catch (error) {
    console.error(error);
    elements.list.innerHTML = '<p class="empty">Les données ne peuvent pas être chargées. Vérifiez que le portail est servi par un serveur web.</p>';
    document.querySelector("#updatedAt").textContent = "Données indisponibles";
  }
}

let searchTimer;
elements.search.addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(applyFilters, 160); });
elements.layer.addEventListener("change", () => {
  elements.commune.value = "";
  elements.year.value = "";
  populateFilters();
  document.querySelector(".map-note").classList.toggle("is-cadastration", elements.layer.value === "cadastrations");
  document.querySelector("#map").classList.toggle("cadastration-mode", elements.layer.value === "cadastrations");
  document.querySelector("#mapNoteText").textContent = elements.layer.value === "cadastrations"
    ? "Jaune : terrain à faire · Orange : cadastration en attente · Bleu : terrain fait."
    : "Les dossiers sans coordonnées restent accessibles dans la liste.";
  applyFilters();
});
elements.commune.addEventListener("change", applyFilters);
elements.year.addEventListener("change", applyFilters);
elements.location.addEventListener("change", applyFilters);
elements.reset.addEventListener("click", () => { elements.search.value = ""; elements.commune.value = ""; elements.year.value = ""; elements.location.value = ""; applyFilters(); });
elements.loadMore.addEventListener("click", () => { state.visible += 30; renderList(); });
document.querySelector(".dialog-close").addEventListener("click", () => elements.dialog.close());
document.querySelector("#showOnMap").addEventListener("click", showSelectedOnMap);
elements.dialog.addEventListener("click", (event) => { if (event.target === elements.dialog) elements.dialog.close(); });
start();
