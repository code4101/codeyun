const BRIDGE_VERSION = "0.1.0";
const BRIDGE_URL = "http://127.0.0.1:8000/api/project-graph/runtime/snapshot";
const SESSION_ID = crypto.randomUUID();
const POLL_INTERVAL_MS = 2000;

async function optionalProperty(proxyObject, propertyName) {
  try {
    return await proxyObject[propertyName];
  } catch {
    return undefined;
  }
}

async function proxyUuid(value) {
  if (value === undefined || value === null) return undefined;
  const proxyObject = await value;
  return await optionalProperty(proxyObject, "uuid");
}

async function proxyUuidList(values) {
  if (!Array.isArray(values)) return undefined;
  const uuids = [];
  for (const value of values) {
    const uuid = await proxyUuid(value);
    if (typeof uuid === "string" && uuid.length > 0) uuids.push(uuid);
  }
  return uuids;
}

async function readRectangle(proxyObject) {
  const collisionBox = await optionalProperty(proxyObject, "collisionBox");
  if (!collisionBox) return {};
  try {
    const rectangle = await collisionBox.getRectangle();
    const location = await rectangle.location;
    const size = await rectangle.size;
    return {
      position: { x: Number(await location.x), y: Number(await location.y) },
      size: { x: Number(await size.x), y: Number(await size.y) },
    };
  } catch {
    return {};
  }
}

async function readRuntimeObject(value) {
  const proxyObject = await value;
  const uuid = await optionalProperty(proxyObject, "uuid");
  if (typeof uuid !== "string" || uuid.length === 0) return null;

  const text = await optionalProperty(proxyObject, "text");
  const sourceUuid = await proxyUuid(await optionalProperty(proxyObject, "source"));
  const targetUuid = await proxyUuid(await optionalProperty(proxyObject, "target"));
  const children = await optionalProperty(proxyObject, "children");
  const childUuids = await proxyUuidList(children);
  const attachmentId = await optionalProperty(proxyObject, "attachmentId");
  const url = await optionalProperty(proxyObject, "url");
  const rectangle = await readRectangle(proxyObject);

  let kind = "Unknown";
  if (sourceUuid || targetUuid) kind = "Edge";
  else if (childUuids !== undefined) kind = "Section";
  else if (typeof attachmentId === "string") kind = "ImageNode";
  else if (url !== undefined) kind = "UrlNode";
  else if (text !== undefined) kind = "TextNode";

  return {
    uuid,
    kind,
    text: typeof text === "string" ? text : null,
    ...rectangle,
    source_uuid: sourceUuid,
    target_uuid: targetUuid,
    child_uuids: childUuids,
    attachment_id: typeof attachmentId === "string" ? attachmentId : null,
  };
}

async function readSelectedUuids(project) {
  try {
    const stageManager = await project.stageManager;
    const selectedEntities = await stageManager.getSelectedEntities();
    const selectedAssociations = await stageManager.getSelectedAssociations();
    return await proxyUuidList([...selectedEntities, ...selectedAssociations]);
  } catch {
    return [];
  }
}

async function readCurrentSnapshot() {
  const project = await prg.tabs_getCurrentProject();
  if (!project) return null;

  const uri = await project.uri;
  const projectUri = uri ? await uri.toString() : "";
  const stage = await project.stage;
  const objects = [];
  for (const value of stage) {
    const object = await readRuntimeObject(value);
    if (object) objects.push(object);
  }

  return {
    bridge_version: BRIDGE_VERSION,
    session_id: SESSION_ID,
    observed_at: Date.now() / 1000,
    project_uri: projectUri,
    project_title: String((await project.title) ?? ""),
    project_state: String((await optionalProperty(project, "projectState")) ?? ""),
    objects,
    selected_uuids: (await readSelectedUuids(project)) ?? [],
  };
}

async function publishCurrentSnapshot() {
  const snapshot = await readCurrentSnapshot();
  if (!snapshot) return;
  const response = await prg.fetch(BRIDGE_URL, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(snapshot),
  });
  const ok = await response.ok;
  if (!ok) {
    const status = await response.status;
    throw new Error(`CodeYun runtime bridge returned HTTP ${status}: ${await response.text()}`);
  }
}

async function poll() {
  try {
    await publishCurrentSnapshot();
  } catch (error) {
    console.warn("[CodeYun Runtime Reader]", error);
  } finally {
    setTimeout(poll, POLL_INTERVAL_MS);
  }
}

void poll();
