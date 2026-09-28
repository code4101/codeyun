import assert from 'node:assert/strict'
import test from 'node:test'
import { build } from 'esbuild'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

const compiled = await build({
  stdin: { contents: `export * from './integrations/project-graph/overlay/selectionDetails.tsx'; export {Entity} from '@/core/stage/stageObject/abstract/StageEntity';`, resolveDir: path.dirname(frontend) },
  bundle: true, write: false, format: 'esm', platform: 'node',
  plugins: [{ name: 'public-project-fixture', setup(builder) {
    builder.onResolve({ filter: /^@\// }, args => ({ path: args.path, namespace: 'fixture' }))
    builder.onResolve({ filter: /\/plateValue$/ }, () => ({ path: path.join(frontend, 'src/components/rich-text/plateValue.ts') }))
    builder.onLoad({ filter: /.*/, namespace: 'fixture' }, ({ path }) => ({ contents:
      path.endsWith('StageEntity') ? 'export class Entity {}' :
      path.endsWith('utilsControl') ? 'export class ControllerUtils {}' :
      'export class Project {}; export const ProjectState={Unsaved:1};'
    }))
  }}],
})
const { Entity, SelectionDetailsService, configureDetails, updateNodeDetails } = await import(`data:text/javascript;base64,${Buffer.from(compiled.outputFiles[0].text).toString('base64')}`)

test('selection snapshots avoid idle body traversal and own-edit echoes; external replacement still publishes', () => {
  const entity = new Entity()
  Object.assign(entity, { uuid: 'a', text: 'A', isSelected: true, details: [{ type: 'p', children: [{ text: 'body' }] }] })
  let traversals = 0
  entity.details.toJSON = () => { traversals++; throw new Error('idle serialization') }
  const published = [], objects = [entity]
  const project = { stageManager: { getStageObjects: () => objects, getEntities: () => objects },
    syncAssociationManager: { syncFrom() {} }, historyManager: { recordStep() {} } }
  configureDetails(true, value => published.push(value))
  const service = new SelectionDetailsService(project)
  const tick = () => { service.nextCheck = 0; service.tick() }
  tick()
  for (let i = 0; i < 100; i++) tick()
  assert.equal(published.length, 1)
  assert.equal(traversals, 0)
  delete entity.details.toJSON
  updateNodeDetails(project, 'a', [{ type: 'p', children: [{ text: 'edited' }] }])
  tick()
  assert.equal(published.length, 1, 'local edit must not reload the body editor')
  entity.details = [{ type: 'p', children: [{ text: 'undo' }] }]
  tick()
  assert.equal(published.length, 2)
  entity.text = 'Renamed'; tick()
  assert.equal(published.at(-1).title, 'Renamed')
  entity.isSelected = false; tick()
  assert.equal(published.at(-1), null)
  entity.isSelected = true; tick()
  assert.equal(published.at(-1).id, 'a')
  configureDetails(false); entity.text = 'Hidden'; tick()
  assert.equal(published.at(-1).title, 'Renamed')
  service.dispose()
})
