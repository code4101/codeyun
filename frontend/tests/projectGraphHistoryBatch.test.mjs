import assert from 'node:assert/strict';
import test from 'node:test';
import { build } from 'esbuild';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const compiled = await build({
  entryPoints: [path.join(root, 'integrations/project-graph/overlay/galleryHistory.ts')],
  bundle: true, write: false, format: 'esm', platform: 'node',
  plugins: [{ name: 'history-project-fixture', setup(builder) {
    builder.onResolve({ filter: /^@\// }, args => ({ path: args.path, namespace: 'fixture' }));
    builder.onResolve({ filter: /^@graphif\/serializer$/ }, args => ({ path: args.path, namespace: 'fixture' }));
    builder.onResolve({ filter: /^\.\/graphDocument$/ }, args => ({ path: args.path, namespace: 'fixture' }));
    builder.onLoad({ filter: /.*/, namespace: 'fixture' }, ({ path: name }) => ({ contents:
      name.endsWith('StageHistoryManager') ? 'export class HistoryManager {}' :
      name.endsWith('Settings') ? 'export const Settings={historySize:100};' :
      name === './graphDocument' ? `export const captureStage=p=>structuredClone(p.objects);
        export const applyObjects=(p,before,next)=>{p.objects=structuredClone(next)};` :
      name === '@graphif/serializer' ? 'export const deserialize=v=>v;' :
      'export class Project {}; export const ProjectState={Unsaved:1};'
    }));
  }}],
});
const { GalleryHistory } = await import(`data:text/javascript;base64,${Buffer.from(compiled.outputFiles[0].text).toString('base64')}`);

test('one selection deletion undoes every intermediate node/edge removal together', () => {
  const original = { node: { text: 'Original' }, edge1: { source: 'node' }, edge2: { source: 'node' } };
  const project = { objects: structuredClone(original), emit() {} };
  const history = new GalleryHistory(project);
  project.objects.node.text = 'Edited'; history.recordStep();
  const beforeDelete = structuredClone(project.objects);
  history.edit(() => {
    for (const id of ['node', 'edge1', 'edge2']) { delete project.objects[id]; history.recordStep(); }
  });
  assert.deepEqual(project.objects, {});
  history.undo(); assert.deepEqual(project.objects, beforeDelete);
  history.undo(); assert.deepEqual(project.objects, original, 'previous edit remains a separate undo step');
  history.redo(); history.redo(); assert.deepEqual(project.objects, {});
});

test('a protected-object no-op creates no undo step', () => {
  const project = { objects: { node: { text: 'Original' } }, emit() {} };
  const history = new GalleryHistory(project);
  project.objects.node.text = 'Edited'; history.recordStep();
  history.edit(() => { history.recordStep(); });
  history.undo(); assert.equal(project.objects.node.text, 'Original');
});
