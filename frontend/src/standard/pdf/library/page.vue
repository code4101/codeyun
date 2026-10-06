<script setup lang="ts">
import ReaderWorkspace from './ReaderWorkspace.vue'
import { useReaderWorkspace } from './useReaderWorkspace'
const workspace = useReaderWorkspace()
workspace.enterLibrary()
</script>
<template><ReaderWorkspace standalone /></template>
