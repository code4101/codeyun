import test from 'node:test'
import assert from 'node:assert/strict'
import { createFramePacer } from '../../integrations/project-graph/overlay/framePacer.ts'

test('focus FPS changes do not stall background rendering or cause a catch-up burst', () => {
  let fps=60, paints=0
  const advance=createFramePacer(()=>paints++,()=>fps)
  for(let time=0;time<=6000;time+=10) advance(time)
  const focused=paints
  fps=30
  for(let time=6010;time<=6200;time+=10) advance(time)
  assert.ok(paints-focused>=5 && paints-focused<=6)
  const background=paints
  fps=60
  for(let time=6210;time<=6400;time+=10) advance(time)
  assert.ok(paints-background>=10 && paints-background<=12)
})
test('resuming after suspension bounds work and then continues normally', () => {
  let paints=0
  const advance=createFramePacer(()=>paints++,()=>60)
  advance(0);advance(60000)
  assert.equal(paints,10)
  advance(60017)
  assert.equal(paints,11)
})
