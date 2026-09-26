import { test } from 'node:test'
import assert from 'node:assert/strict'
import { displayGroup, parseMapDisplay, showBasemapLabel } from './mapDisplay'

test('city toggle includes capitals and settlements without hiding country or province labels', () => {
  for (const id of ['label_city', 'label_city_capital', 'label_town', 'label_village', 'label_other']) {
    assert.equal(displayGroup({ id, type: 'symbol' }), 'cities')
  }
  assert.equal(displayGroup({ id: 'label_country_1', type: 'symbol' }), 'countries')
  assert.equal(displayGroup({ id: 'label_state', type: 'symbol' }), 'regions')
  assert.equal(displayGroup({ id: 'country-selection', type: 'fill' }), null)
  assert.equal(displayGroup({ id: 'poi_r1', type: 'symbol', 'source-layer': 'poi' }), 'places')
  assert.equal(displayGroup({ id: 'highway-name-major', type: 'symbol', 'source-layer': 'transportation_name' }), 'roads')
  assert.equal(displayGroup({ id: 'water_name_line_label', type: 'symbol', 'source-layer': 'water_name' }), 'water')
})

test('preferences accept booleans only and recover from missing or invalid storage', () => {
  assert.equal(parseMapDisplay('{"cities":false}').cities, false)
  assert.equal(parseMapDisplay('{"cities":"false"}').cities, true)
  assert.equal(parseMapDisplay('broken').countries, true)
  assert.equal(parseMapDisplay('null').countries, true)
  assert.equal(parseMapDisplay(null).countries, true)
})


test('administrative hierarchy replaces independent basemap province and settlement labels', () => {
  const settings=parseMapDisplay(null)
  assert.equal(showBasemapLabel('cities',settings,true),false)
  assert.equal(showBasemapLabel('regions',settings,true),false)
  assert.equal(showBasemapLabel('countries',settings,true),true)
  assert.equal(showBasemapLabel('cities',settings,false),false)
  assert.equal(showBasemapLabel('cities',{...settings,autoRegions:false},true),true)
})


test('one road switch hides transport geometry and labels, preserving the existing off preference', () => {
  const settings=parseMapDisplay('{"roads":false}')
  assert.equal(parseMapDisplay(null).roads,false)
  for (const [id,type] of [['road_minor','line'],['bridge_motorway','line'],['tunnel_minor','line'],['road_area_pattern','fill'],['road_one_way_arrow','symbol']]) {
    const key=displayGroup({id:id!,type:type!,'source-layer':'transportation'})!
    assert.equal(key,'roads')
    assert.equal(showBasemapLabel(key,settings,false),false)
  }
  assert.equal(displayGroup({id:'admin-borders',type:'line'}),null)
  assert.equal(displayGroup({id:'waterway',type:'line','source-layer':'waterway'}),null)
  assert.equal(showBasemapLabel('roads',settings,false),false)
  assert.equal(showBasemapLabel('roads',{...settings,roads:true},false),true)
})
