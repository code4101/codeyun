<template>
  <div class="music-tools-page">
    <section class="page-head">
      <div class="title-block">
        <h1>音乐工具</h1>
        <el-radio-group v-model="activeView" class="view-tabs" size="small" @change="handleViewChange">
          <el-radio-button label="workspace">音频解析</el-radio-button>
          <el-radio-button label="instruments">乐器资料表</el-radio-button>
        </el-radio-group>
      </div>
      <el-tag :type="toolInfo?.demucs_installed ? 'success' : 'warning'" effect="plain">
        {{ toolStatusText }}
      </el-tag>
    </section>

    <template v-if="activeView === 'workspace'">
    <section class="command-bar">
      <div class="command-mode">
        <button
          type="button"
          :class="{ active: commandMode === 'separate' }"
          @click="commandMode = 'separate'"
        >
          分轨
        </button>
        <button
          type="button"
          :class="{ active: commandMode === 'humming' }"
          @click="commandMode = 'humming'"
        >
          哼唱转谱
        </button>
        <button
          type="button"
          :class="{ active: commandMode === 'multitrack' }"
          @click="activateMultitrackMode"
        >
          真实分轨 ZIP
        </button>
      </div>

      <div v-if="commandMode === 'separate'" class="command-fields">
        <label class="file-picker command-file">
          <input type="file" accept="audio/*,video/mp4,video/x-m4v,video/quicktime,video/webm,video/x-matroska,video/x-msvideo" @change="handleFileChange" />
          <span>{{ selectedFile ? selectedFile.name : '选择音频或视频文件' }}</span>
        </label>
        <el-select v-model="selectedEngine" class="engine-select" :disabled="task?.running" title="分离模式">
          <el-option label="四轨分离" value="demucs" />
          <el-option label="六轨细分" value="audio_separator_6s" :disabled="!toolInfo?.audio_separator_installed" />
        </el-select>
        <el-button type="primary" :disabled="!selectedFile || task?.running" :loading="task?.running" @click="startSeparation">
          分离音轨
        </el-button>
        <el-button
          plain
          :disabled="!canReparseActiveJob || task?.running"
          :loading="task?.running && taskJobId === selectedJobId"
          title="保留原始文件，清掉旧分轨和旧谱面后重新解析"
          @click="reparseActiveJob"
        >
          重新解析
        </el-button>
      </div>

      <div v-else class="command-fields">
        <template v-if="commandMode === 'humming'">
        <el-button
          :type="isRecording ? 'danger' : 'default'"
          :disabled="task?.running || !canRecordHumming"
          :loading="recordingStarting"
          @click="toggleRecording"
        >
          <el-icon><Microphone /></el-icon>
          {{ isRecording ? '停止录音' : '录音' }}
        </el-button>
        <label class="file-picker command-file humming-picker">
          <input type="file" accept="audio/*,video/webm,video/mp4,video/quicktime" @change="handleHummingFileChange" />
          <span>{{ hummingFile ? hummingFile.name : recordedHummingUrl ? '已录制一段哼唱' : '选择哼唱音频' }}</span>
        </label>
        <el-input-number v-model="hummingTempoBpm" class="tempo-input" :min="40" :max="240" :step="1" controls-position="right" title="BPM" />
        <el-input-number v-model="hummingBeatsPerBar" class="beats-input" :min="2" :max="12" :step="1" controls-position="right" title="每小节拍数" />
        <el-button
          type="primary"
          :disabled="!hummingFile || task?.running || isRecording"
          :loading="task?.running"
          @click="transcribeHumming"
        >
          生成主旋律
        </el-button>
        <audio v-if="recordedHummingUrl" class="recording-preview" :src="recordedHummingUrl" controls />
        </template>
        <template v-else>
          <label class="file-picker command-file">
            <input type="file" accept=".zip,application/zip,application/x-zip-compressed" @change="handleMultitrackZipChange" />
            <span>{{ multitrackZipFile ? multitrackZipFile.name : '选择真实分轨 ZIP' }}</span>
          </label>
          <el-input
            v-model="multitrackZipUrl"
            class="multitrack-url-input"
            clearable
            placeholder="或粘贴 ZIP 直链"
          />
          <el-select v-model="selectedMultitrackSourceId" class="engine-select multitrack-source-select" title="素材来源">
            <el-option label="手动下载的分轨包" value="" />
            <el-option
              v-for="source in multitrackSources"
              :key="source.id"
              :label="source.name"
              :value="source.id"
            />
          </el-select>
          <el-button type="primary" :disabled="(!multitrackZipFile && !multitrackZipUrl.trim()) || multitrackImporting" :loading="multitrackImporting" @click="importSelectedMultitrackZip">
            导入试听
          </el-button>
        </template>
      </div>
    </section>

    <section v-if="commandMode === 'multitrack'" class="multitrack-source-panel">
      <div class="multitrack-source-head">
        <strong>真实分轨素材来源</strong>
        <span>优先选完整真实歌曲；需要听清单件乐器时，再用独奏轨/古典数据集。</span>
      </div>
      <div class="multitrack-source-list">
        <a
          v-for="source in multitrackSources"
          :key="source.id"
          class="multitrack-source-card"
          :href="source.url"
          target="_blank"
          rel="noreferrer"
        >
          <span>{{ source.kind }}</span>
          <strong>{{ source.name }}</strong>
          <em v-if="source.fit">{{ source.fit }}</em>
          <p>{{ source.import_hint }}</p>
        </a>
      </div>
      <div v-if="featuredMultitrackWorks.length" class="multitrack-work-list">
        <div class="multitrack-work-title">推荐先听</div>
        <div
          v-for="work in featuredMultitrackWorks"
          :key="work.key"
          class="multitrack-work-item"
          :class="{ active: selectedMultitrackWorkKey === work.key }"
          role="button"
          tabindex="0"
          @click="selectMultitrackWork(work.key)"
          @keydown.enter="selectMultitrackWork(work.key)"
        >
          <div class="multitrack-work-main">
            <span>{{ work.level }}</span>
            <strong>{{ work.title }}</strong>
            <em>{{ work.focus }}</em>
          </div>
          <div class="multitrack-work-instruments">
            <span v-for="instrument in work.instruments" :key="`${work.title}:${instrument}`">{{ instrument }}</span>
          </div>
          <p>{{ work.why }}</p>
          <p>{{ work.study }}</p>
          <p class="style-bridge">{{ work.style_bridge }}</p>
          <a :href="work.sourceUrl" target="_blank" rel="noreferrer" @click.stop>{{ work.sourceName }}</a>
        </div>
      </div>
      <div v-if="selectedMultitrackWork" class="multitrack-study-panel">
        <div class="multitrack-study-head">
          <div>
            <span>{{ selectedMultitrackWork.sourceName }}</span>
            <strong>{{ selectedMultitrackWork.title }}</strong>
          </div>
          <div class="multitrack-study-actions">
            <button type="button" @click="copyPrompt(selectedMultitrackStudyText)">复制学习清单</button>
            <button type="button" @click="openSelectedMultitrackSource">打开下载页</button>
          </div>
        </div>
        <div class="multitrack-study-grid">
          <div>
            <span>为什么选</span>
            <p>{{ selectedMultitrackWork.why }}</p>
          </div>
          <div>
            <span>导入方式</span>
            <p>{{ selectedMultitrackImportHint }}</p>
          </div>
          <div>
            <span>拆听顺序</span>
            <p>{{ selectedMultitrackWork.study }}</p>
          </div>
          <div>
            <span>风格迁移</span>
            <p>{{ selectedMultitrackWork.style_bridge }}</p>
          </div>
        </div>
        <div class="multitrack-study-steps">
          <span v-for="step in selectedMultitrackSteps" :key="step">{{ step }}</span>
        </div>
      </div>
    </section>

    <el-alert
      v-if="task"
      class="task-alert"
      :type="task.status === 'failed' ? 'error' : task.status === 'completed' ? 'success' : 'info'"
      :closable="false"
      :title="task.status === 'failed' ? task.error || task.message : task.message"
    />

    <section class="workspace-layout">
      <aside class="history-pane">
        <div class="pane-title">解析历史</div>
        <div v-if="!jobs.length" class="history-empty">暂无历史</div>
        <button
          v-for="job in jobs"
          :key="job.job_id"
          class="history-item"
          :class="{ active: job.job_id === selectedJobId }"
          type="button"
          :title="job.error || job.task_message || job.filename"
          @click="selectJob(job)"
          @contextmenu.prevent="renameHistoryJob(job)"
        >
          <span class="history-name">{{ job.filename }}</span>
          <span class="history-meta">
            {{ statusText(job.status) }} · {{ formatDate(job.updated_at || job.created_at) }}
          </span>
        </button>
      </aside>

      <section v-if="audioFiles.length" class="workspace">
        <div class="active-title">
          <div class="active-name">{{ activeJob?.filename || '当前音频' }}</div>
          <div class="active-meta">{{ activeJob?.model || 'htdemucs' }}</div>
        </div>

        <div class="transport">
          <el-button circle :title="isPlaying ? '暂停' : '播放'" @click="togglePlayback">
            <el-icon><VideoPause v-if="isPlaying" /><VideoPlay v-else /></el-icon>
          </el-button>
          <span class="time-text">{{ formatTime(currentTime) }}</span>
          <el-slider
            v-model="currentTime"
            class="timeline"
            :min="0"
            :max="duration || 1"
            :step="0.05"
            :show-tooltip="false"
            @change="seekTo"
          />
          <span class="time-text">{{ formatTime(duration) }}</span>
        </div>

        <div class="stem-table">
          <div v-for="track in visibleTracks" :key="track.key" class="stem-row" :class="{ 'original-stem': track.key === 'original' }">
            <div class="stem-control-row">
              <el-switch
                v-model="track.enabled"
                class="stem-switch"
                @change="handleTrackToggle(track.key)"
              />
              <div class="stem-heading">
                <span class="stem-title">{{ track.label }}</span>
                <span class="stem-file">{{ getTrackFilename(track.key) || '未生成' }}</span>
              </div>
              <el-slider
                v-model="track.volume"
                class="volume-slider"
                :min="0"
                :max="1"
                :step="0.01"
                :show-tooltip="false"
                @input="applyTrackVolume(track.key)"
              />
            </div>
            <div class="stem-wave-row">
              <button
                class="waveform"
                type="button"
                :disabled="!getTrackFilename(track.key)"
                @click="handleWaveformClick($event, track.key)"
              >
                <span
                  v-for="(peak, index) in getWaveformPeaks(track.key)"
                  :key="`${track.key}:${index}`"
                  class="waveform-bar"
                  :style="{ height: `${Math.max(2, Math.round(peak * 28))}px` }"
                />
                <span
                  v-if="duration > 0"
                  class="waveform-playhead"
                  :style="{ left: `${Math.min(100, Math.max(0, (currentTime / duration) * 100))}%` }"
                />
              </button>
            </div>
          </div>
        </div>

        <section class="creative-brief-panel">
          <div class="creative-brief-head">
            <div>
              <div class="creative-title">音乐描述 / Suno 提示词</div>
              <div class="creative-meta">按当前音频和分轨结果生成，可继续手工改写成古风、纯音乐、影视配乐方向。</div>
            </div>
            <el-button size="small" plain :loading="creativeBriefLoading" @click="loadCreativeBrief">
              生成描述
            </el-button>
          </div>
          <div v-if="manualCopyText" class="manual-copy-panel">
            <div class="manual-copy-head">
              <span>浏览器限制自动复制，已保留文本</span>
              <button type="button" @click="manualCopyText = ''">收起</button>
            </div>
            <textarea :value="manualCopyText" readonly @focus="$event.target.select()" />
          </div>
          <div v-if="creativeBrief" class="creative-brief-body">
            <p>{{ creativeBrief.description_zh }}</p>
            <div class="creative-tags">
              <span v-for="tag in creativeBrief.tags" :key="tag">{{ tag }}</span>
            </div>
            <div v-if="creativeFeatureItems.length" class="feature-grid">
              <div v-for="item in creativeFeatureItems" :key="item.label">
                <span>{{ item.label }}</span>
                <strong>{{ item.value }}</strong>
              </div>
            </div>
            <div v-if="creativeStyleProfile" class="style-profile-panel">
              <div class="style-profile-head">
                <div>
                  <span>推荐方向</span>
                  <strong>{{ creativeStyleProfile.best_fit }}</strong>
                </div>
                <button type="button" @click="copyPrompt(creativeStyleProfileCopyText)">复制画像</button>
              </div>
              <div v-if="creativeStyleProfile.analysis_tags?.length" class="style-profile-tags">
                <span v-for="tag in creativeStyleProfile.analysis_tags" :key="`profile:${tag}`">{{ tag }}</span>
              </div>
              <div v-if="creativeStyleProfile.why?.length" class="style-profile-reasons">
                <p v-for="reason in creativeStyleProfile.why" :key="reason">{{ reason }}</p>
              </div>
              <div v-if="creativeStyleScores.length" class="style-score-list">
                <div v-for="item in creativeStyleScores" :key="item.name">
                  <span>{{ item.name }}</span>
                  <strong>{{ item.score }}</strong>
                </div>
              </div>
            </div>
            <div v-if="creativeSections.length" class="section-strip">
              <div
                v-for="section in creativeSections"
                :key="`${section.name}:${section.start_second}`"
                class="section-segment"
                :class="`energy-${section.energy}`"
                :style="{ flexGrow: section.width }"
              >
                <span>{{ section.name }}</span>
                <strong>{{ section.energy }}</strong>
              </div>
            </div>
            <div v-if="creativeStyleDirections.length" class="style-direction-row">
              <button
                v-for="direction in creativeStyleDirections"
                :key="direction.key"
                type="button"
                :class="{ active: direction.key === selectedCreativeStyleKey }"
                @click="selectedCreativeStyleKey = direction.key"
              >
                {{ direction.name }}
              </button>
            </div>
            <div v-if="selectedCreativeStyle" class="style-direction-meta">
              <span>{{ selectedCreativeStyle.use_case }}</span>
              <strong>{{ selectedCreativeStyle.palette.join(' / ') }}</strong>
            </div>
            <div v-if="creativeSunoFieldItems.length" class="suno-field-grid">
              <div v-for="item in creativeSunoFieldItems" :key="item.label" class="suno-field-card">
                <div class="prompt-label">
                  <span>{{ item.label }}</span>
                  <button type="button" @click="copyPrompt(item.copyText)">复制</button>
                </div>
                <p>{{ item.text }}</p>
              </div>
            </div>
            <div v-if="creativeStylePresets.length" class="style-preset-panel">
              <div class="prompt-record-title">固定风格路线</div>
              <div class="style-preset-tabs">
                <button
                  v-for="preset in creativeStylePresets"
                  :key="preset.key"
                  type="button"
                  :class="{ active: preset.key === selectedCreativePreset?.key }"
                  @click="selectedCreativePresetKey = preset.key"
                >
                  {{ preset.name }}
                </button>
              </div>
              <div v-if="selectedCreativePreset" class="style-preset-detail">
                <div class="style-preset-main">
                  <div>
                    <strong>{{ selectedCreativePreset.name }}</strong>
                    <span>{{ selectedCreativePreset.fit }}</span>
                  </div>
                  <div class="platform-open-actions">
                    <button type="button" @click="copyAndOpenPreset(selectedCreativePreset, 'suno')">投到 Suno</button>
                    <button type="button" @click="copyAndOpenPreset(selectedCreativePreset, 'udio')">投到 Udio</button>
                  </div>
                </div>
                <div class="style-preset-palette">
                  <span v-for="item in selectedCreativePreset.palette" :key="`${selectedCreativePreset.key}:palette:${item}`">{{ item }}</span>
                </div>
                <div class="style-preset-grid">
                  <div>
                    <span>回听检查</span>
                    <p>{{ selectedCreativePreset.listen_check.join(' / ') }}</p>
                  </div>
                  <div>
                    <span>Suno Style</span>
                    <p>{{ selectedCreativePreset.suno_style }}</p>
                  </div>
                </div>
                <div class="creative-recipe-actions">
                  <button type="button" @click="copyPrompt(selectedCreativePreset.suno_prompt)">复制 Suno</button>
                  <button type="button" @click="copyPrompt(presetPackageText(selectedCreativePreset, 'suno'))">复制 Suno 包</button>
                  <button type="button" @click="copyPrompt(selectedCreativePreset.udio_prompt)">复制 Udio</button>
                  <button type="button" @click="copyPrompt(presetPackageText(selectedCreativePreset, 'udio'))">复制 Udio 包</button>
                  <button type="button" @click="copyPrompt(selectedCreativePreset.negative)">复制规避项</button>
                  <button type="button" @click="savePresetPromptVersion(selectedCreativePreset, 'suno')">保存 Suno</button>
                  <button type="button" @click="savePresetPromptVersion(selectedCreativePreset, 'udio')">保存 Udio</button>
                </div>
              </div>
            </div>
            <div v-if="creativeRecipes.length" class="creative-recipe-list">
              <div class="prompt-record-title">创作方案</div>
              <div v-for="recipe in creativeRecipes" :key="recipe.key" class="creative-recipe-item">
                <div class="creative-recipe-head">
                  <div>
                    <strong>{{ recipe.title }}</strong>
                    <span>{{ recipe.goal }}</span>
                  </div>
                  <div class="platform-open-actions">
                    <button type="button" @click="copyAndOpenRecipe(recipe, 'suno')">投到 Suno</button>
                    <button type="button" @click="copyAndOpenRecipe(recipe, 'udio')">投到 Udio</button>
                  </div>
                </div>
                <p>{{ recipe.hook }}</p>
                <div class="creative-recipe-tags">
                  <span v-for="tag in recipe.style_tags" :key="`${recipe.key}:tag:${tag}`">{{ tag }}</span>
                </div>
                <div class="creative-recipe-columns">
                  <div>
                    <span>配器</span>
                    <p>{{ recipe.instrumentation.join(' / ') }}</p>
                  </div>
                  <div>
                    <span>先听</span>
                    <p>{{ recipe.listen_first.join(' / ') }}</p>
                  </div>
                </div>
                <ol class="creative-recipe-moves">
                  <li v-for="move in recipe.arrangement_moves" :key="`${recipe.key}:move:${move}`">{{ move }}</li>
                </ol>
                <div class="creative-recipe-actions">
                  <button type="button" @click="saveRecipePromptVersion(recipe, 'suno')">保存 Suno</button>
                  <button type="button" @click="saveRecipePromptVersion(recipe, 'udio')">保存 Udio</button>
                  <button type="button" @click="copyPrompt(recipePackageText(recipe, 'suno'))">复制 Suno 包</button>
                  <button type="button" @click="copyPrompt(recipe.platform_prompts.suno_style || '')">复制 Style</button>
                  <button type="button" @click="copyPrompt(recipe.platform_prompts.udio_prompt || '')">复制 Udio</button>
                  <button type="button" @click="copyPrompt(recipePackageText(recipe, 'udio'))">复制 Udio 包</button>
                  <button type="button" @click="copyPrompt(recipe.platform_prompts.negative || '')">复制规避项</button>
                </div>
              </div>
            </div>
            <div class="prompt-grid">
              <div>
                <div class="prompt-label">
                  <span>中文提示词</span>
                  <span class="prompt-actions">
                    <button type="button" @click="copyPrompt(currentCreativePromptZh)">复制</button>
                    <button type="button" @click="saveCurrentCreativePrompt">保存</button>
                  </span>
                </div>
                <p>{{ currentCreativePromptZh }}</p>
              </div>
              <div>
                <div class="prompt-label">
                  <span>English Prompt</span>
                  <span class="prompt-actions">
                    <button type="button" @click="copyPrompt(currentCreativePromptEn)">复制</button>
                    <button type="button" @click="saveCurrentCreativePrompt">保存</button>
                  </span>
                </div>
                <p>{{ currentCreativePromptEn }}</p>
              </div>
            </div>
            <div v-if="creativeArrangementPlan.length" class="arrangement-plan">
              <div class="prompt-record-title">编曲拆解</div>
              <div v-for="item in creativeArrangementPlan" :key="item.section" class="arrangement-item">
                <span>{{ item.section }} · {{ item.energy }}</span>
                <p>{{ item.listen }}</p>
                <strong>{{ item.arrange }}</strong>
              </div>
            </div>
            <div v-if="creativeStemInsights.length" class="stem-insight-list">
              <div class="prompt-record-title">分轨听法</div>
              <div v-for="item in creativeStemInsights" :key="item.stem" class="stem-insight-item">
                <div>
                  <strong>{{ item.label }}</strong>
                  <span>{{ item.role }}</span>
                </div>
                <p>{{ item.focus }}</p>
                <p>{{ item.usage }}</p>
              </div>
            </div>
            <div v-if="creativePromptRecords.length" class="prompt-record-list">
              <div class="prompt-record-title">已保存版本</div>
              <div v-for="record in creativePromptRecords" :key="record.id" class="prompt-record-item">
                <div>
                  <strong>{{ record.name }}</strong>
                  <span>{{ formatDate(record.created_at) }} · {{ record.source }}</span>
                </div>
                <button type="button" @click="copyPrompt(record.prompt_zh)">复制</button>
              </div>
            </div>
          </div>
        </section>

        <section v-if="scoreInfos.length || isPianoStemScoreKind(selectedScoreKind)" class="score-panel">
          <div class="score-toolbar">
            <div class="score-left">
              <el-button
                circle
                size="small"
                :disabled="!scoreNotes.length"
                :title="isScorePlaying ? '暂停谱面演奏' : '演奏谱面'"
                @click="toggleScorePlayback"
              >
                <el-icon><VideoPause v-if="isScorePlaying" /><VideoPlay v-else /></el-icon>
              </el-button>
              <div class="score-heading">
                <span class="score-title">{{ scorePanelTitle }}</span>
                <span class="score-meta">{{ scorePanelMeta }}</span>
              </div>
            </div>
            <div class="score-tools">
              <el-radio-group v-model="selectedScoreKind" class="score-mode-tabs" size="small" @change="handleScoreKindChange">
                <el-radio-button
                  v-for="mode in scoreModes"
                  :key="mode.kind"
                  :label="mode.kind"
                >
                  {{ mode.label }}
                </el-radio-button>
              </el-radio-group>
              <div class="score-actions">
                <a v-if="scoreFileUrl('musicxml')" class="score-link" :href="scoreFileUrl('musicxml')" target="_blank">MusicXML</a>
                <a v-if="scoreFileUrl('midi')" class="score-link" :href="scoreFileUrl('midi')" target="_blank">MIDI</a>
              </div>
            </div>
          </div>
          <div v-if="scoreNotes.length" class="score-transport">
            <span class="time-text">{{ formatTime(scoreCurrentTime) }}</span>
            <el-slider
              v-model="scoreCurrentTime"
              class="timeline"
              :min="0"
              :max="scoreDuration || 1"
              :step="0.05"
              :show-tooltip="false"
              @change="seekScoreTo"
            />
            <span class="time-text">{{ formatTime(scoreDuration) }}</span>
          </div>
          <div v-if="jianpuDraft" class="jianpu-line">{{ jianpuDraft }}</div>
          <div v-if="scoreNotes.length" class="piano-roll" :style="{ '--visible-keys': pianoKeyCount }">
            <div class="roll-lane">
              <span
                v-for="note in visibleScoreNotes"
                :key="note.id"
                class="roll-note"
                :class="[note.hand, { active: note.active }]"
                :style="{
                  left: `${note.left}%`,
                  width: `${note.width}%`,
                  top: `${note.top}px`,
                  height: `${note.height}px`,
                }"
              />
            </div>
            <div class="piano-keyboard">
              <span
                v-for="key in pianoKeys"
                :key="key.note"
                class="piano-key"
                :class="{ black: key.black, active: activeScoreNotes.has(key.note) }"
                :title="key.name"
              />
            </div>
          </div>
          <div v-else class="score-empty">
            {{ isPianoStemScoreKind(selectedScoreKind) ? pianoStemScoreEmptyText : '这个解析结果还没有可演奏谱面。' }}
          </div>
        </section>

        <section v-else-if="scoreLoading" class="score-panel score-loading">
          正在加载钢琴独奏谱
        </section>

        <audio
          v-for="file in audioFiles"
          :key="`${selectedJobId}:${file.stem}`"
          :ref="(el) => setAudioRef(file.stem, el)"
          :src="file.url"
          preload="metadata"
          @loadedmetadata="handleMetadata(file.stem)"
          @timeupdate="handleTimeUpdate(file.stem)"
          @ended="handleEnded(file.stem)"
        />
      </section>

      <section v-else class="empty-state">
        <div class="empty-title">上传音频或视频后开始分轨</div>
        <div class="empty-text">解析完成后会保存到历史，刷新页面也可以继续试听。</div>
      </section>
    </section>
    </template>

    <section v-else class="instrument-registry-panel">
      <div class="registry-toolbar">
        <div class="registry-summary">
          <span class="registry-title">乐器资料表</span>
          <span class="registry-meta">
            {{ instrumentRegistry ? `${instrumentRegistry.total} 条 · ${instrumentRegistry.generated_at}` : '未加载' }}
          </span>
        </div>
        <el-input
          v-model="instrumentSearch"
          class="registry-search"
          clearable
          placeholder="搜索乐器、中文名、别名"
        />
        <el-select v-model="instrumentSourceFilter" class="registry-select" clearable placeholder="来源">
          <el-option
            v-for="source in instrumentSources"
            :key="source"
            :label="translateInstrumentSource(source)"
            :value="source"
          />
        </el-select>
        <el-select v-model="instrumentRoleFilter" class="registry-select" clearable placeholder="角色">
          <el-option
            v-for="role in instrumentRoles"
            :key="role"
            :label="translateInstrumentRole(role)"
            :value="role"
          />
        </el-select>
        <el-select v-model="instrumentColumnMode" class="registry-select" placeholder="分类体系">
          <el-option label="现代发声分类" value="modern" />
          <el-option label="中国八音" value="bayin" />
        </el-select>
      </div>
      <div v-if="instrumentRegistry" class="registry-source-row">
        <span v-for="(count, source) in instrumentRegistry.source_counts" :key="source">
          {{ translateInstrumentSource(source) }} {{ count }}
        </span>
      </div>
      <NoteSplitView
        class="instrument-split-view"
        :top-height="instrumentStructurePaneHeight"
        :show-editor="true"
        empty-description="暂无乐器明细"
        :editor-min-height="220"
        @resize-start="startInstrumentPaneResizing"
      >
        <template #main>
          <section class="instrument-pivot-panel">
            <div class="pivot-title-row">
              <div>
                <div class="pivot-title">乐器透视图</div>
                <div class="pivot-subtitle">左侧是用途角色，上方是{{ instrumentColumnMode === 'bayin' ? '中国八音' : '发声分类' }}；点击乐器查看说明。</div>
              </div>
              <div class="pivot-count">{{ filteredInstruments.length }} 个乐器</div>
            </div>
            <div v-if="!instrumentPivotRows.length" class="pivot-empty">没有符合筛选条件的乐器</div>
            <div v-else class="pivot-scroll">
              <table class="pivot-table">
                <thead>
                  <tr>
                    <th class="pivot-role-head">角色</th>
                    <th v-for="topClass in instrumentPivotColumns" :key="topClass">
                      {{ topClass }}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="row in instrumentPivotRows"
                    :key="row.role"
                    :class="{ 'is-collapsed': isInstrumentRoleCollapsed(row.role) }"
                  >
                    <th class="pivot-role-cell">
                      <button
                        type="button"
                        class="role-toggle"
                        :title="isInstrumentRoleCollapsed(row.role) ? '展开这个角色' : '折叠这个角色'"
                        @click.stop="toggleInstrumentRole(row.role)"
                      >
                        <span class="role-toggle-mark">{{ isInstrumentRoleCollapsed(row.role) ? '+' : '-' }}</span>
                        <span class="role-toggle-name">{{ row.role }}</span>
                        <span class="role-toggle-count">{{ row.count }}</span>
                      </button>
                    </th>
                    <td v-for="topClass in instrumentPivotColumns" :key="`${row.role}:${topClass}`">
                      <div v-if="!isInstrumentRoleCollapsed(row.role)" class="pivot-cell-tree">
                        <div
                          v-for="group in row.cells[topClass] || []"
                          :key="group.key"
                          class="cell-tree-group"
                        >
                          <button
                            type="button"
                            class="cell-group-toggle"
                            :title="isInstrumentCellGroupExpanded(group.key) ? '折叠这个分类' : '展开这个分类'"
                            @click.stop="toggleInstrumentCellGroup(group.key)"
                          >
                            <span class="cell-group-mark">{{ isInstrumentCellGroupExpanded(group.key) ? '-' : '+' }}</span>
                            <span class="cell-group-title">{{ group.title }}</span>
                            <span class="cell-group-count">{{ group.count }}</span>
                          </button>
                          <div v-if="isInstrumentCellGroupExpanded(group.key)" class="pivot-cell-list">
                            <button
                              v-for="instrument in group.instruments"
                              :key="instrument.id"
                              class="instrument-chip"
                              :class="{ active: selectedInstrument?.id === instrument.id }"
                              type="button"
                              @mousedown.stop
                              @click.stop="selectInstrument(instrument)"
                            >
                              {{ chineseInstrumentName(instrument) }}
                            </button>
                          </div>
                        </div>
                      </div>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
        </template>

        <template #editor>
          <div class="instrument-detail-pane">
            <section class="instrument-detail-card">
              <div class="instrument-detail-head">
                <div v-if="selectedInstrument">
                  <div class="instrument-detail-title">{{ chineseInstrumentName(selectedInstrument) }}</div>
                  <div class="instrument-detail-subtitle">{{ selectedInstrument.name }}</div>
                </div>
                <div v-else>
                  <div class="instrument-detail-title">乐器详情</div>
                  <div class="instrument-detail-subtitle">点击上方乐器查看资料</div>
                </div>
                <el-button v-if="selectedInstrument" size="small" plain @click="selectedInstrument = null">关闭</el-button>
              </div>
              <div v-if="selectedInstrument" class="instrument-kv-list">
                <div class="instrument-kv-item">
                  <span class="detail-label">中文名</span>
                  <span>{{ chineseInstrumentName(selectedInstrument) }}</span>
                </div>
                <div class="instrument-kv-item">
                  <span class="detail-label">英文名</span>
                  <span>{{ selectedInstrument.name }}</span>
                </div>
                <div class="instrument-kv-item">
                  <span class="detail-label">常见别名</span>
                  <span>{{ compactList(selectedInstrument.aliases, 12) || '暂无' }}</span>
                </div>
                <div class="instrument-kv-item">
                  <span class="detail-label">用途角色</span>
                  <span>{{ roleSummary(selectedInstrument) || '未知' }}</span>
                </div>
                <div class="instrument-kv-item">
                  <span class="detail-label">发声分类</span>
                  <span>{{ topClassSummary(selectedInstrument) || '未分类' }}</span>
                </div>
                <div class="instrument-kv-item">
                  <span class="detail-label">八音分类</span>
                  <span>{{ bayinSummary(selectedInstrument) || '未归类' }}</span>
                </div>
                <div class="instrument-kv-item">
                  <span class="detail-label">分类编号</span>
                  <span>{{ hornbostelSummary(selectedInstrument) || '暂无' }}</span>
                </div>
                <div class="instrument-kv-item">
                  <span class="detail-label">谱表/软件</span>
                  <span>{{ musescoreSummary(selectedInstrument) || '暂无' }}</span>
                </div>
                <div class="instrument-kv-item">
                  <span class="detail-label">播放音色</span>
                  <span>{{ gmSummary(selectedInstrument) || '暂无' }}</span>
                </div>
                <div class="instrument-kv-item">
                  <span class="detail-label">资料来源</span>
                  <span>{{ sourceSummary(selectedInstrument) || '暂无' }}</span>
                </div>
                <div class="instrument-kv-item">
                  <span class="detail-label">说明</span>
                  <span>{{ instrumentDescription(selectedInstrument) }}</span>
                </div>
              </div>
              <div v-else class="instrument-detail-empty">
                从上方透视图里选择一个乐器。
              </div>
            </section>
          </div>
        </template>
      </NoteSplitView>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Microphone, VideoPause, VideoPlay } from '@element-plus/icons-vue'
import NoteSplitView from '@/components/NoteSplitView.vue'
import {
  getMusicInstrumentRegistry,
  getMusicJobCreativeBrief,
  getMusicSeparationTask,
  getMusicToolInfo,
  importMultitrackZip,
  importMultitrackZipUrl,
  listMultitrackLibrary,
  listMusicJobCreativePrompts,
  listMusicJobScores,
  listMusicJobs,
  rerunMusicJob,
  saveMusicJobCreativePrompt,
  startHummingTranscription,
  startMusicSeparation,
  updateMusicJob,
  type MusicInstrumentRecord,
  type MusicAudioFile,
  type MusicCreativeBrief,
  type MusicCreativePromptRecord,
  type MusicInstrumentRegistry,
  type MultitrackLibrarySource,
  type MusicSeparationEngine,
  type MusicJob,
  type MusicScoreInfo,
  type MusicStem,
  type MusicTaskPayload,
  type MusicToolInfo,
} from '@/api/musicTools'
import { useResizablePane } from '@/utils/useResizablePane'

interface StemTrack {
  key: MusicStem
  label: string
  enabled: boolean
  volume: number
}

interface StoredTrackPreference {
  enabled?: boolean
  volume?: number
}

interface StoredJobWorkspace {
  currentTime?: number
  scoreKind?: ScoreKind
  separatedEnabledSnapshot?: Partial<Record<MusicStem, boolean>>
  tracks?: Partial<Record<MusicStem, StoredTrackPreference>>
}

interface StoredWorkspace {
  selectedJobId?: string
  selectedEngine?: MusicSeparationEngine
  jobs?: Record<string, StoredJobWorkspace>
}

interface ScoreNote {
  id: string
  start: number
  end: number
  note: number
  velocity: number
  hand: string
}

interface PianoKey {
  note: number
  name: string
  black: boolean
}

interface InstrumentCellGroup {
  key: string
  title: string
  count: number
  instruments: MusicInstrumentRecord[]
}

interface InstrumentPivotRow {
  role: string
  count: number
  cells: Record<string, InstrumentCellGroup[]>
}

interface CreativeSection {
  name: string
  start_second: number
  end_second: number
  energy: string
  average: number
  width: number
}

type ScoreKind = 'piano_solo_score' | 'piano_stem_transcription' | 'piano_stem_transcription_clean' | 'melody_skeleton'
type MusicToolView = 'workspace' | 'instruments'
type CommandMode = 'separate' | 'humming' | 'multitrack'
type CreativeRecipe = MusicCreativeBrief['creative_recipes'][number]
type CreativeStylePreset = NonNullable<MusicCreativeBrief['style_presets']>[number]
type GenerationPlatform = 'suno' | 'udio'
type InstrumentColumnMode = 'modern' | 'bayin'

const STEM_LABELS: Record<MusicStem, string> = {
  original: '原曲',
  vocals: '人声',
  other: '伴奏/其他',
  bass: '贝斯',
  drums: '鼓',
  guitar: '吉他',
  piano: '钢琴',
}

const WORKSPACE_STORAGE_KEY = 'codeyun.music-tools.workspace.v2'
const INSTRUMENT_ROLE_COLLAPSE_STORAGE_KEY = 'codeyun.music-tools.instrument-role-collapse.v2'
const INSTRUMENT_CELL_GROUP_COLLAPSE_STORAGE_KEY = 'codeyun.music-tools.instrument-cell-group-collapse.v1'
const SEPARATED_STEMS: MusicStem[] = ['vocals', 'other', 'bass', 'drums', 'guitar', 'piano']
const DEFAULT_EXPECTED_STEMS: MusicStem[] = ['vocals', 'other', 'bass', 'drums']
const DEFAULT_TRACKS: StemTrack[] = [
  { key: 'original', label: STEM_LABELS.original, enabled: true, volume: 0.85 },
  { key: 'vocals', label: STEM_LABELS.vocals, enabled: false, volume: 0.9 },
  { key: 'other', label: STEM_LABELS.other, enabled: false, volume: 0.9 },
  { key: 'bass', label: STEM_LABELS.bass, enabled: false, volume: 0.9 },
  { key: 'drums', label: STEM_LABELS.drums, enabled: false, volume: 0.9 },
  { key: 'guitar', label: STEM_LABELS.guitar, enabled: false, volume: 0.9 },
  { key: 'piano', label: STEM_LABELS.piano, enabled: false, volume: 0.9 },
]
const SCORE_KIND_LABELS: Record<ScoreKind, string> = {
  piano_solo_score: '整曲独奏谱',
  piano_stem_transcription: '钢琴轨扒谱',
  piano_stem_transcription_clean: '钢琴轨清洗版',
  melody_skeleton: '主旋律草稿',
}

const INSTRUMENT_SOURCE_LABELS: Record<string, string> = {
  hornbostel_sachs: '乐器分类',
  mimo_translations: '多语言译名',
  musescore: 'MuseScore 谱表',
  musicbrainz: 'MusicBrainz 资料',
  general_midi: '通用 MIDI',
}

const INSTRUMENT_ROLE_LABELS: Record<string, string> = {
  melody: '旋律',
  harmony: '和声',
  rhythm: '节奏',
  bass: '低音',
  texture: '织体',
  unknown: '未知',
}

const HS_TOP_CLASS_LABELS: Record<string, string> = {
  idiophones: '体鸣',
  membranophones: '膜鸣',
  chordophones: '弦鸣',
  aerophones: '气鸣',
  electrophones: '电鸣',
}

const BAYIN_KEYWORDS: Record<string, string[]> = {
  金: ['钟', '铃', '钹', '镲', '锣', '铙', '铜', '金属', '钢片', 'bell', 'gong', 'cymbal', 'triangle', 'metallophone', 'steel', 'bronze'],
  石: ['磬', '石', '钟乳石', 'stone', 'lithophone'],
  土: ['埙', '缶', '陶', '瓦', '土', 'clay', 'ocarina'],
  革: ['鼓', '鼗', 'drum', 'timpani', 'tambourine', 'bongo', 'conga', 'djembe', 'tabla', 'bodhran'],
  丝: ['琴', '瑟', '筝', '琵琶', '阮', '胡', '弦', 'harp', 'zither', 'lute', 'violin', 'viola', 'cello', 'bass', 'guitar', 'banjo', 'mandolin', 'fiddle', 'string'],
  木: ['木鱼', '木琴', '梆', '柝', '板', '拍板', '木', 'woodblock', 'xylophone', 'claves', 'castanet', 'slit drum'],
  匏: ['笙', '竽', '葫芦丝', '匏', 'sheng', 'hulusi', 'gourd'],
  竹: ['笛', '箫', '篪', '管子', '竹', 'bamboo', 'flute', 'recorder', 'pipe'],
}

const BAYIN_GROUP_LABELS: Record<string, string> = {
  木管: '竹',
  管乐: '竹',
  簧管: '竹',
  自由簧: '匏',
  铜管: '金',
  弓弦: '丝',
  拨弦: '丝',
  弦乐: '丝',
  吉他: '丝',
  贝斯: '丝',
  合奏: '丝',
  钢琴: '丝',
  有音高打击乐: '木',
  无音高打击乐: '革',
  打击乐: '革',
  民族乐器: '未归类',
  合成器: '未归类',
  合成器主奏: '未归类',
  合成器铺底: '未归类',
  合成器效果: '未归类',
  音效: '未归类',
  人声: '未归类',
  键盘: '未归类',
}

const GM_FAMILY_LABELS: Record<string, string> = {
  Piano: '钢琴',
  'Chromatic Percussion': '有音高打击乐',
  Organ: '风琴',
  Guitar: '吉他',
  Bass: '贝斯',
  Strings: '弦乐',
  Ensemble: '合奏',
  Brass: '铜管',
  Reed: '簧管',
  Pipe: '管乐',
  'Synth Lead': '合成器主奏',
  'Synth Pad': '合成器铺底',
  'Synth Effects': '合成器效果',
  Ethnic: '民族乐器',
  Percussive: '打击乐',
  'Sound Effects': '音效',
}

const MUSESCORE_GROUP_LABELS: Record<string, string> = {
  Woodwinds: '木管',
  Brass: '铜管',
  'Strings - Bowed': '弓弦',
  'Strings - Plucked': '拨弦',
  'Percussion - Pitched': '有音高打击乐',
  'Percussion - Unpitched': '无音高打击乐',
  Keyboards: '键盘',
  'Free Reed': '自由簧',
  Voices: '人声',
  Synthesizers: '合成器',
}

const DEFAULT_COLLAPSED_INSTRUMENT_ROLES = ['旋律', '低音', '节奏', '和声', '织体', '未知']
const GENERATION_PLATFORM_URLS: Record<GenerationPlatform, string> = {
  suno: 'https://suno.com/create',
  udio: 'https://www.udio.com/create',
}

const activeView = ref<MusicToolView>('workspace')
const commandMode = ref<CommandMode>('separate')
const route = useRoute()
const toolInfo = ref<MusicToolInfo | null>(null)
const selectedFile = ref<File | null>(null)
const hummingFile = ref<File | null>(null)
const multitrackZipFile = ref<File | null>(null)
const multitrackZipUrl = ref('')
const multitrackImporting = ref(false)
const multitrackSources = ref<MultitrackLibrarySource[]>([])
const selectedMultitrackSourceId = ref('')
const selectedMultitrackWorkKey = ref('')
const hummingTempoBpm = ref(96)
const hummingBeatsPerBar = ref(4)
const isRecording = ref(false)
const recordingStarting = ref(false)
const recordedHummingUrl = ref('')
const mediaRecorder = ref<MediaRecorder | null>(null)
const recordingStream = ref<MediaStream | null>(null)
const recordingChunks: Blob[] = []
const selectedEngine = ref<MusicSeparationEngine>('demucs')
const task = ref<MusicTaskPayload | null>(null)
const jobs = ref<MusicJob[]>([])
const selectedJobId = ref('')
const pollTimer = ref<number | null>(null)
const audioRefs = new Map<MusicStem, HTMLAudioElement>()
const waveformCache = reactive<Record<string, number[]>>({})
const waveformLoading = reactive<Record<string, boolean>>({})
const isPlaying = ref(false)
const currentTime = ref(0)
const duration = ref(0)
const scoreInfos = ref<MusicScoreInfo[]>([])
const scoreInfo = ref<MusicScoreInfo | null>(null)
const scoreNotes = ref<ScoreNote[]>([])
const scoreLoading = ref(false)
const selectedScoreKind = ref<ScoreKind>('piano_solo_score')
const isScorePlaying = ref(false)
const scoreCurrentTime = ref(0)
const creativeBrief = ref<MusicCreativeBrief | null>(null)
const creativeBriefLoading = ref(false)
const creativePromptRecords = ref<MusicCreativePromptRecord[]>([])
const creativePromptSaving = ref(false)
const selectedCreativeStyleKey = ref('base')
const selectedCreativePresetKey = ref('')
const manualCopyText = ref('')
const workspacePrefs = ref<StoredWorkspace>({})
const persistTimer = ref<number | null>(null)
const isRestoringWorkspace = ref(false)
const scoreAudioContext = ref<AudioContext | null>(null)
const scoreAnimationFrame = ref<number | null>(null)
const scorePlaybackStartedAt = ref(0)
const scorePlaybackBaseTime = ref(0)
const scheduledScoreNotes = new Set<string>()
const instrumentRegistry = ref<MusicInstrumentRegistry | null>(null)
const instrumentLoading = ref(false)
const instrumentSearch = ref('')
const instrumentSourceFilter = ref('')
const instrumentRoleFilter = ref('')
const instrumentColumnMode = ref<InstrumentColumnMode>('modern')
const selectedInstrument = ref<MusicInstrumentRecord | null>(null)
const collapsedInstrumentRoles = ref<string[]>([...DEFAULT_COLLAPSED_INSTRUMENT_ROLES])
const expandedInstrumentCellGroups = ref<string[]>([])

const tracks = reactive<StemTrack[]>(DEFAULT_TRACKS.map((track) => ({ ...track })))

const activeJob = computed(() => jobs.value.find((job) => job.job_id === selectedJobId.value) || null)
const creativeFeatureItems = computed(() => {
  const features = creativeBrief.value?.audio_features || {}
  if (!features.available) return []
  const items = [
    { label: '估计 BPM', value: features.estimated_bpm },
    { label: '可能调性', value: features.estimated_key_zh || features.estimated_key },
    { label: '能量', value: features.energy_label },
    { label: '情绪', value: features.mood_label },
    { label: '音色明暗', value: features.brightness_label },
    { label: '结构', value: features.arrangement_shape },
    { label: '峰值', value: typeof features.peak_second === 'number' ? `${features.peak_second}s` : '' },
    { label: '动态范围', value: features.dynamic_range },
    { label: '低频占比', value: features.low_frequency_ratio },
    { label: '静音比例', value: features.silence_ratio },
    { label: '分析时长', value: features.analyzed_seconds ? `${features.analyzed_seconds}s` : '' },
  ]
  return items.filter((item) => item.value !== null && item.value !== undefined && item.value !== '')
})
const creativeSections = computed<CreativeSection[]>(() => {
  const sections = creativeBrief.value?.audio_features?.sections
  if (!Array.isArray(sections)) return []
  return sections
    .map((section) => {
      if (!section || typeof section !== 'object') return null
      const item = section as Record<string, unknown>
      return {
        name: String(item.name || ''),
        start_second: Number(item.start_second || 0),
        end_second: Number(item.end_second || 0),
        energy: String(item.energy || ''),
        average: Number(item.average || 0),
        width: Math.max(0.1, Number(item.width || 0.25)),
      }
    })
    .filter((section): section is CreativeSection => Boolean(section?.name && section.energy))
})
const creativeStyleProfile = computed(() => creativeBrief.value?.style_profile || null)
const creativeStyleScores = computed(() => creativeStyleProfile.value?.style_scores || [])
const creativeStyleProfileCopyText = computed(() => {
  const profile = creativeStyleProfile.value
  if (!profile) return ''
  const blueprint = profile.prompt_blueprint || {}
  const style = Array.isArray(blueprint.suno_style) ? blueprint.suno_style.join(', ') : ''
  const reasons = Array.isArray(profile.why) ? profile.why.join('；') : ''
  const workflow = Array.isArray(profile.workflow) ? profile.workflow.join(' / ') : ''
  return [
    `推荐方向：${profile.best_fit || ''}`,
    style ? `Style：${style}` : '',
    blueprint.prompt_core_zh ? `中文核心：${blueprint.prompt_core_zh}` : '',
    blueprint.prompt_core_en ? `English core: ${blueprint.prompt_core_en}` : '',
    reasons ? `原因：${reasons}` : '',
    workflow ? `流程：${workflow}` : '',
  ].filter(Boolean).join('\n')
})
const creativeStyleDirections = computed(() => creativeBrief.value?.style_directions || [])
const selectedCreativeStyle = computed(() => {
  const directions = creativeStyleDirections.value
  return directions.find((direction) => direction.key === selectedCreativeStyleKey.value) || directions[0] || null
})
const currentCreativePromptZh = computed(() => selectedCreativeStyle.value?.prompt_zh || creativeBrief.value?.suno_prompt_zh || '')
const currentCreativePromptEn = computed(() => selectedCreativeStyle.value?.prompt_en || creativeBrief.value?.suno_prompt_en || '')
const creativeStemInsights = computed(() => creativeBrief.value?.stem_insights || [])
const creativeArrangementPlan = computed(() => creativeBrief.value?.arrangement_plan || [])
const creativeRecipes = computed(() => creativeBrief.value?.creative_recipes || [])
const creativeStylePresets = computed(() => creativeBrief.value?.style_presets || [])
const selectedCreativePreset = computed(() => {
  const presets = creativeStylePresets.value
  return presets.find((preset) => preset.key === selectedCreativePresetKey.value) || presets[0] || null
})
const creativeSunoFieldItems = computed(() => {
  const fields = creativeBrief.value?.suno_fields || {}
  const join = (items: unknown) => Array.isArray(items) ? items.map((item) => String(item || '').trim()).filter(Boolean).join(', ') : ''
  const rows = [
    { label: '标题候选', text: join(fields.title_ideas) },
    { label: 'Style Tags', text: join(fields.style_tags) },
    { label: 'Mood / Energy', text: join(fields.mood_tags) },
    { label: '结构约束', text: join(fields.structure_tags) },
    { label: '规避项', text: String(fields.negative_prompt || '') },
    { label: '纯音乐提示', text: String(fields.instrumental_hint || '') },
  ]
  return rows
    .filter((row) => row.text)
    .map((row) => ({ ...row, copyText: row.text }))
})
const featuredMultitrackWorks = computed(() =>
  multitrackSources.value.flatMap((source) =>
    (source.featured_works || []).map((work, index) => ({
      ...work,
      key: `${source.id}:${index}:${work.title}`,
      sourceId: source.id,
      sourceName: source.name,
      sourceUrl: source.url,
      sourceKind: source.kind,
      sourceHint: source.import_hint,
    })),
  ),
)
const selectedMultitrackWork = computed(() =>
  featuredMultitrackWorks.value.find((work) => work.key === selectedMultitrackWorkKey.value) || featuredMultitrackWorks.value[0] || null,
)
const selectedMultitrackImportHint = computed(() => {
  const work = selectedMultitrackWork.value
  if (!work) return ''
  if (work.sourceId === 'urmp') {
    return '下载数据集里对应 piece 的 individual audio / assembled mix / MIDI，整理成 ZIP 后导入；它最适合古典乐器独听。'
  }
  if (work.sourceId === 'telefunken-live-from-the-lab') {
    return '打开 Live From The Lab 对应演出页，下载 multitrack 包；导入后优先从鼓、贝斯、和声乐器、人声/主奏逐步打开。'
  }
  if (work.sourceId === 'cambridge-mt') {
    return '打开 Cambridge-MT 曲库，下载 Full Multitrack ZIP；如果页面只提供工程包，也可把 wav/aiff 轨道重新压成 ZIP 后导入。'
  }
  return work.sourceHint || '下载或整理包含多条音频轨的 ZIP，然后在上方选择来源并导入试听。'
})
const selectedMultitrackSteps = computed(() => {
  const work = selectedMultitrackWork.value
  if (!work) return []
  return [
    `1. 打开 ${work.sourceName} 下载页。`,
    '2. 下载 Full Multitrack / stems / individual tracks。没有直链时手动下载，不绕过源站限制。',
    '3. 把所有 wav/mp3/aiff 音轨放进一个 ZIP，在上方导入试听。',
    '4. 先只开低音/节奏，再加和声层，最后加主奏或人声，记录每轨职责。',
  ]
})
const selectedMultitrackStudyText = computed(() => {
  const work = selectedMultitrackWork.value
  if (!work) return ''
  return [
    `真实分轨学习素材：${work.title}`,
    `来源：${work.sourceName}`,
    `下载页：${work.sourceUrl}`,
    `类型：${work.sourceKind}`,
    `重点：${work.focus}`,
    `乐器：${work.instruments.join(' / ')}`,
    `为什么选：${work.why}`,
    `导入方式：${selectedMultitrackImportHint.value}`,
    `拆听顺序：${work.study}`,
    `风格迁移：${work.style_bridge}`,
    ...selectedMultitrackSteps.value,
  ].join('\n')
})
const toolStatusText = computed(() => {
  if (!toolInfo.value?.demucs_installed) {
    return '分轨工具未安装'
  }
  return toolInfo.value.audio_separator_installed ? '四轨/六轨已安装' : '四轨已安装'
})
const taskJobId = computed(() => task.value?.metadata.job_id || task.value?.result?.job_id || '')
const taskFiles = computed(() => task.value?.result?.files || task.value?.metadata.files || [])
const audioFiles = computed(() => {
  if (task.value && taskJobId.value === selectedJobId.value && taskFiles.value.length) {
    return taskFiles.value
  }
  return activeJob.value?.files || []
})
const expectedStems = computed(() => {
  if (task.value && taskJobId.value === selectedJobId.value && task.value.metadata.expected_stems?.length) {
    return task.value.metadata.expected_stems
  }
  if (Array.isArray(activeJob.value?.expected_stems)) {
    return activeJob.value.expected_stems
  }
  return DEFAULT_EXPECTED_STEMS
})
const canReparseActiveJob = computed(() => {
  if (!activeJob.value || activeJob.value.status === 'queued' || activeJob.value.status === 'running') {
    return false
  }
  if (activeJob.value.input_kind === 'score_demo' || activeJob.value.input_kind === 'humming') {
    return false
  }
  if (selectedEngine.value === 'audio_separator_6s' && !toolInfo.value?.audio_separator_installed) {
    return false
  }
  return true
})
const visibleTracks = computed(() => {
  const visible = new Set<MusicStem>(['original', ...expectedStems.value])
  for (const file of audioFiles.value) {
    visible.add(file.stem)
  }
  return tracks.filter((track) => visible.has(track.key))
})
const hasPianoTrack = computed(() => audioFiles.value.some((file) => file.stem === 'piano'))
const isPianoStemScoreKind = (kind: ScoreKind) =>
  kind === 'piano_stem_transcription' || kind === 'piano_stem_transcription_clean'
const scoreModes = computed(() =>
  (Object.keys(SCORE_KIND_LABELS) as ScoreKind[]).map((kind) => ({
    kind,
    label: SCORE_KIND_LABELS[kind],
    available: scoreInfos.value.some((score) => score.kind === kind),
  })),
)
const scorePanelTitle = computed(() => {
  if (scoreInfo.value) return scoreInfo.value.title
  if (selectedScoreKind.value === 'piano_stem_transcription_clean') return '钢琴轨清洗版'
  if (selectedScoreKind.value === 'piano_stem_transcription') return '钢琴轨扒谱'
  return selectedScoreKind.value === 'melody_skeleton' ? '主旋律草稿' : '整曲独奏谱'
})
const scorePanelMeta = computed(() => {
  if (scoreInfo.value) {
    const parts = [
      scoreInfo.value.version,
      scoreInfo.value.beats_per_bar ? `${scoreInfo.value.beats_per_bar}拍` : '',
      scoreInfo.value.measures ? `${scoreInfo.value.measures} 小节` : '',
    ].filter(Boolean)
    return parts.join(' · ') || SCORE_KIND_LABELS[selectedScoreKind.value]
  }
  return isPianoStemScoreKind(selectedScoreKind.value)
    ? (hasPianoTrack.value ? '已切到 piano.mp3，等待生成 MIDI' : '当前结果没有钢琴音轨')
    : '未生成'
})
const pianoStemScoreEmptyText = computed(() =>
  hasPianoTrack.value
    ? '当前还没有钢琴轨 MIDI。已切到只听 piano.mp3，可以先判断分离出的钢琴是否干净。'
    : '当前分轨结果没有 piano.mp3，需要先用六轨细分重新分离。',
)
const canRecordHumming = computed(() => typeof navigator !== 'undefined' && Boolean(navigator.mediaDevices?.getUserMedia))
const scoreDuration = computed(() => Math.max(0, ...scoreNotes.value.map((note) => note.end)))
const scoreLookAheadSeconds = 5
const pianoMinNote = 28
const pianoMaxNote = 84
const pianoKeyCount = pianoMaxNote - pianoMinNote + 1
const uniqueStrings = (items: Array<string | number | null | undefined>) =>
  Array.from(new Set(items.map((item) => String(item || '').trim()).filter(Boolean)))

const compactList = (items: Array<string | number | null | undefined>, limit = 3) => {
  const values = uniqueStrings(items)
  if (values.length <= limit) {
    return values.join(' / ')
  }
  return `${values.slice(0, limit).join(' / ')} +${values.length - limit}`
}

const translateInstrumentSource = (source: string) => INSTRUMENT_SOURCE_LABELS[source] || source

const translateInstrumentRole = (role: string) => INSTRUMENT_ROLE_LABELS[role] || role

const chineseInstrumentName = (row: MusicInstrumentRecord) => compactList(row.zh_names?.length ? row.zh_names : [row.name], 4)

const chineseTopClass = (value: string) => {
  const text = String(value || '').trim()
  if (!text) return ''
  const parts = text.split('/').map((part) => part.trim()).filter(Boolean)
  const chinesePart = parts.find((part) => /[\u4e00-\u9fa5]/.test(part))
  if (chinesePart) return chinesePart
  return HS_TOP_CLASS_LABELS[text.toLowerCase()] || text
}

const instrumentRoleLabels = (item: MusicInstrumentRecord) =>
  uniqueStrings((item.derived_roles?.length ? item.derived_roles : ['unknown']).map(translateInstrumentRole))

const instrumentTopClassLabels = (item: MusicInstrumentRecord) => {
  const labels = uniqueStrings((item.hs_top_classes || []).map(chineseTopClass))
  return labels.length ? labels : ['未分类']
}

const instrumentBayinLabels = (item: MusicInstrumentRecord) => {
  const modernGroups = uniqueStrings([
    ...(item.musescore || []).map((entry) => MUSESCORE_GROUP_LABELS[entry.group] || entry.group),
    ...(item.general_midi || []).map((entry) => GM_FAMILY_LABELS[entry.family] || entry.family),
    item.playback.gm_family ? GM_FAMILY_LABELS[item.playback.gm_family] || item.playback.gm_family : '',
  ])
  const text = [
    item.name,
    ...item.zh_names,
    ...item.aliases,
    ...item.hs_top_classes,
    ...(item.hornbostel_sachs || []).map((entry) => entry.label),
    ...modernGroups,
  ].join(' ').toLowerCase()
  for (const [label, keywords] of Object.entries(BAYIN_KEYWORDS)) {
    if (keywords.some((keyword) => text.includes(keyword.toLowerCase()))) {
      return [label]
    }
  }
  const groupLabels = uniqueStrings(modernGroups.map((group) => BAYIN_GROUP_LABELS[group]).filter((label) => label && label !== '未归类'))
  if (groupLabels.length) return groupLabels
  const topClasses = instrumentTopClassLabels(item)
  if (topClasses.includes('弦鸣')) return ['丝']
  if (topClasses.includes('膜鸣')) return ['革']
  if (topClasses.includes('气鸣')) return ['竹']
  if (topClasses.includes('体鸣')) return ['木']
  return ['未归类']
}

const instrumentColumnLabels = (item: MusicInstrumentRecord) =>
  instrumentColumnMode.value === 'bayin' ? instrumentBayinLabels(item) : instrumentTopClassLabels(item)

const roleSummary = (item: MusicInstrumentRecord) => compactList(instrumentRoleLabels(item), 6)

const topClassSummary = (item: MusicInstrumentRecord) => compactList(instrumentTopClassLabels(item), 6)

const bayinSummary = (item: MusicInstrumentRecord) => compactList(instrumentBayinLabels(item), 4)

const hornbostelSummary = (item: MusicInstrumentRecord) =>
  compactList(item.hornbostel_sachs.map((entry) => `${entry.code} ${entry.label}`), 4)

const musescoreSummary = (item: MusicInstrumentRecord) =>
  compactList(
    item.musescore.map((entry) => {
      const group = MUSESCORE_GROUP_LABELS[entry.group] || entry.group
      return entry.staves ? `${group} / ${entry.staves}` : group
    }),
    5,
  )

const gmSummary = (item: MusicInstrumentRecord) =>
  compactList(
    item.general_midi.map((entry) => `${entry.program} ${GM_FAMILY_LABELS[entry.family] || entry.family}`),
    5,
  )

const sourceSummary = (item: MusicInstrumentRecord) => compactList(item.sources.map(translateInstrumentSource), 5)

const instrumentSources = computed(() =>
  Array.from(new Set(instrumentRegistry.value?.instruments.flatMap((item) => item.sources) || [])).sort(),
)
const instrumentRoles = computed(() =>
  Array.from(new Set(instrumentRegistry.value?.instruments.flatMap((item) => item.derived_roles) || [])).sort(),
)
const filteredInstruments = computed(() => {
  const query = instrumentSearch.value.trim().toLowerCase()
  return (instrumentRegistry.value?.instruments || []).filter((item) => {
    if (instrumentSourceFilter.value && !item.sources.includes(instrumentSourceFilter.value)) {
      return false
    }
    if (instrumentRoleFilter.value && !item.derived_roles.includes(instrumentRoleFilter.value)) {
      return false
    }
    if (!query) {
      return true
    }
    const haystack = [
      item.name,
      ...item.zh_names,
      ...item.aliases,
      ...item.hs_top_classes,
      ...instrumentBayinLabels(item),
      ...item.derived_roles,
      ...(item.hornbostel_sachs || []).map((entry) => `${entry.code} ${entry.label}`),
    ].join(' ').toLowerCase()
    return haystack.includes(query)
  })
})
const roleOrder = ['旋律', '低音', '节奏', '和声', '织体', '未知']
const topClassOrder = ['弦鸣', '气鸣', '体鸣', '膜鸣', '电鸣', '未分类']
const bayinOrder = ['金', '石', '土', '革', '丝', '木', '匏', '竹', '未归类']
const cellGroupOrder = [
  '键盘',
  '弓弦',
  '拨弦',
  '木管',
  '铜管',
  '自由簧',
  '有音高打击乐',
  '无音高打击乐',
  '打击乐',
  '民族乐器',
  '合成器',
  '人声',
  '音效',
  '其他资料',
]
const sortByOrder = (values: string[], order: string[]) =>
  [...values].sort((a, b) => {
    const aIndex = order.indexOf(a)
    const bIndex = order.indexOf(b)
    if (aIndex !== -1 || bIndex !== -1) {
      return (aIndex === -1 ? 999 : aIndex) - (bIndex === -1 ? 999 : bIndex)
    }
    return a.localeCompare(b, 'zh-Hans-CN')
  })
const instrumentPivotColumns = computed(() =>
  sortByOrder(
    uniqueStrings(filteredInstruments.value.flatMap(instrumentColumnLabels)),
    instrumentColumnMode.value === 'bayin' ? bayinOrder : topClassOrder,
  ),
)
const primaryInstrumentCellGroup = (instrument: MusicInstrumentRecord) => {
  const musescoreGroups = uniqueStrings(
    instrument.musescore.map((entry) => MUSESCORE_GROUP_LABELS[entry.group] || entry.group),
  )
  if (musescoreGroups.length) return musescoreGroups[0]
  const playbackFamily = instrument.playback.gm_family
    ? GM_FAMILY_LABELS[instrument.playback.gm_family] || instrument.playback.gm_family
    : ''
  if (playbackFamily) return playbackFamily
  const gmFamilies = uniqueStrings(instrument.general_midi.map((entry) => GM_FAMILY_LABELS[entry.family] || entry.family))
  if (gmFamilies.length) return gmFamilies[0]
  return '其他资料'
}

const makeInstrumentCellGroupKey = (role: string, topClass: string, group: string) => `${role}::${topClass}::${group}`

const buildInstrumentCellGroups = (
  role: string,
  topClass: string,
  instruments: MusicInstrumentRecord[],
): InstrumentCellGroup[] => {
  const groups = new Map<string, MusicInstrumentRecord[]>()
  for (const instrument of instruments) {
    const group = primaryInstrumentCellGroup(instrument)
    groups.set(group, [...(groups.get(group) || []), instrument])
  }
  return sortByOrder(Array.from(groups.keys()), cellGroupOrder).map((group) => {
    const groupInstruments = [...(groups.get(group) || [])].sort((a, b) =>
      chineseInstrumentName(a).localeCompare(chineseInstrumentName(b), 'zh-Hans-CN'),
    )
    return {
      key: makeInstrumentCellGroupKey(role, topClass, group),
      title: group,
      count: groupInstruments.length,
      instruments: groupInstruments,
    }
  })
}

const instrumentPivotRows = computed<InstrumentPivotRow[]>(() => {
  const roles = sortByOrder(uniqueStrings(filteredInstruments.value.flatMap(instrumentRoleLabels)), roleOrder)
  return roles.map((role) => {
    const rawCells: Record<string, MusicInstrumentRecord[]> = {}
    for (const instrument of filteredInstruments.value) {
      if (!instrumentRoleLabels(instrument).includes(role)) continue
      for (const topClass of instrumentColumnLabels(instrument)) {
        rawCells[topClass] ||= []
        rawCells[topClass].push(instrument)
      }
    }
    const cells: Record<string, InstrumentCellGroup[]> = {}
    for (const topClass of Object.keys(rawCells)) {
      cells[topClass] = buildInstrumentCellGroups(role, topClass, rawCells[topClass])
    }
    const count = filteredInstruments.value.filter((instrument) => instrumentRoleLabels(instrument).includes(role)).length
    return { role, count, cells }
  })
})
const calculateInstrumentPaneBounds = () => {
  const viewportHeight = typeof window === 'undefined' ? 900 : window.innerHeight
  const viewportWidth = typeof window === 'undefined' ? 1200 : window.innerWidth
  const reservedHeight = viewportWidth < 960 ? 230 : 190
  const availableHeight = Math.max(520, viewportHeight - reservedHeight)
  const minDetailHeight = viewportWidth < 960 ? 240 : 280
  const maxHeight = Math.max(260, availableHeight - minDetailHeight)
  const adaptiveHeight = Math.min(maxHeight, Math.max(320, Math.floor(availableHeight * 0.46)))
  return { adaptiveHeight, maxHeight }
}
const {
  paneHeight: instrumentStructurePaneHeight,
  startResizing: startInstrumentPaneResizing,
} = useResizablePane({
  initialHeight: 420,
  getAdaptiveHeight: () => calculateInstrumentPaneBounds().adaptiveHeight,
  getResizeBounds: () => ({
    min: 240,
    max: calculateInstrumentPaneBounds().maxHeight,
  }),
  storageKey: 'music-tools:instrument-registry:split-pane-height:v1',
})
const pianoKeys = computed<PianoKey[]>(() => {
  const blackPitchClasses = new Set([1, 3, 6, 8, 10])
  const names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
  return Array.from({ length: pianoKeyCount }, (_, index) => {
    const note = pianoMinNote + index
    return {
      note,
      name: `${names[note % 12]}${Math.floor(note / 12) - 1}`,
      black: blackPitchClasses.has(note % 12),
    }
  })
})
const activeScoreNotes = computed(() => {
  const now = scoreCurrentTime.value
  return new Set(scoreNotes.value.filter((note) => note.start <= now && note.end >= now).map((note) => note.note))
})
const visibleScoreNotes = computed(() => {
  const now = scoreCurrentTime.value
  const laneHeight = 206
  const maxNoteHeight = 70
  return scoreNotes.value
    .filter((note) => note.note >= pianoMinNote && note.note <= pianoMaxNote)
    .filter((note) => note.end >= now - 0.2 && note.start <= now + scoreLookAheadSeconds)
    .map((note) => {
      const lane = note.note - pianoMinNote
      const left = (lane / pianoKeyCount) * 100
      const width = Math.max(0.8, 100 / pianoKeyCount)
      const startOffset = note.start - now
      const rawBottom = ((scoreLookAheadSeconds - startOffset) / scoreLookAheadSeconds) * laneHeight
      const rawHeight = Math.min(maxNoteHeight, ((note.end - note.start) / scoreLookAheadSeconds) * laneHeight)
      const rawTop = rawBottom - rawHeight
      const top = Math.max(0, rawTop)
      const bottom = Math.min(laneHeight, rawBottom)
      const height = Math.max(0, bottom - top)
      return {
        ...note,
        left,
        width,
        top,
        height,
        active: note.start <= now && note.end >= now,
      }
    })
    .filter((note) => note.height > 0)
})
const jianpuDraft = computed(() => buildJianpuDraft(scoreNotes.value))

const MAJOR_SCALE_PITCH_CLASSES = [0, 2, 4, 5, 7, 9, 11]
const DEGREE_LABELS = ['1', '2', '3', '4', '5', '6', '7']

const inferMajorTonic = (notes: ScoreNote[]) => {
  if (!notes.length) return 0
  const pitchClasses = notes.map((note) => ((Math.round(note.note) % 12) + 12) % 12)
  const lastPitchClass = pitchClasses[pitchClasses.length - 1]
  let bestTonic = 0
  let bestScore = Number.NEGATIVE_INFINITY
  for (let tonic = 0; tonic < 12; tonic += 1) {
    const scale = new Set(MAJOR_SCALE_PITCH_CLASSES.map((step) => (tonic + step) % 12))
    const score =
      pitchClasses.reduce((sum, pitchClass) => sum + (scale.has(pitchClass) ? 1 : -0.4), 0) +
      (lastPitchClass === tonic ? 3 : 0) +
      (lastPitchClass === (tonic + 7) % 12 ? 1 : 0)
    if (score > bestScore) {
      bestScore = score
      bestTonic = tonic
    }
  }
  return bestTonic
}

const noteToJianpu = (note: number, tonic: number) => {
  const pitchClass = ((Math.round(note) % 12) + 12) % 12
  const relative = (pitchClass - tonic + 12) % 12
  const scaleIndex = MAJOR_SCALE_PITCH_CLASSES.indexOf(relative)
  const octaveOffset = Math.floor((Math.round(note) - (60 + tonic)) / 12)
  const octaveMark = octaveOffset > 0 ? "'".repeat(Math.min(3, octaveOffset)) : octaveOffset < 0 ? ",".repeat(Math.min(3, -octaveOffset)) : ''
  if (scaleIndex >= 0) {
    return `${DEGREE_LABELS[scaleIndex]}${octaveMark}`
  }
  const loweredIndex = MAJOR_SCALE_PITCH_CLASSES.indexOf((relative + 1) % 12)
  if (loweredIndex >= 0) {
    return `b${DEGREE_LABELS[loweredIndex]}${octaveMark}`
  }
  const raisedIndex = MAJOR_SCALE_PITCH_CLASSES.indexOf((relative + 11) % 12)
  if (raisedIndex >= 0) {
    return `#${DEGREE_LABELS[raisedIndex]}${octaveMark}`
  }
  return `?${octaveMark}`
}

const buildJianpuDraft = (notes: ScoreNote[]) => {
  if (!notes.length || !scoreInfo.value) return ''
  if (scoreInfo.value.kind !== 'melody_skeleton' && scoreInfo.value.kind !== 'piano_stem_transcription_clean') {
    return ''
  }
  const melody = notes
    .filter((note) => note.end > note.start && note.note >= 36 && note.note <= 96)
    .sort((a, b) => a.start - b.start)
    .slice(0, 80)
  if (!melody.length) return ''
  const tonic = inferMajorTonic(melody)
  const tokens = melody.map((note) => noteToJianpu(note.note, tonic))
  const grouped: string[] = []
  for (let index = 0; index < tokens.length; index += 1) {
    grouped.push(tokens[index])
    if ((index + 1) % 8 === 0 && index !== tokens.length - 1) {
      grouped.push('|')
    }
  }
  return grouped.join(' ')
}

const getTrack = (stem: MusicStem) => tracks.find((track) => track.key === stem)

const trackLabelForFile = (file: MusicAudioFile) => file.label || STEM_LABELS[file.stem] || file.stem

const ensureTracksForAudioFiles = () => {
  const existing = new Set(tracks.map((track) => track.key))
  for (const file of audioFiles.value) {
    if (!file.stem || existing.has(file.stem)) continue
    tracks.push({
      key: file.stem,
      label: trackLabelForFile(file),
      enabled: file.stem !== 'original',
      volume: 0.82,
    })
    existing.add(file.stem)
  }
}

const getSeparatedTracks = () =>
  tracks.filter((track) => track.key !== 'original')

const defaultSeparatedEnabledSnapshot = () =>
  Object.fromEntries(SEPARATED_STEMS.map((stem) => [stem, true])) as Record<MusicStem, boolean>

const currentSeparatedEnabledSnapshot = () =>
  Object.fromEntries(
    SEPARATED_STEMS.map((stem) => [stem, Boolean(getTrack(stem)?.enabled)]),
  ) as Record<MusicStem, boolean>

const getCurrentJobPrefs = () => {
  if (!selectedJobId.value) return null
  return workspacePrefs.value.jobs?.[selectedJobId.value] || null
}

const getSeparatedEnabledSnapshot = () => {
  const snapshot = getCurrentJobPrefs()?.separatedEnabledSnapshot
  if (!snapshot) return defaultSeparatedEnabledSnapshot()
  return Object.fromEntries(
    SEPARATED_STEMS.map((stem) => [stem, snapshot[stem] ?? true]),
  ) as Record<MusicStem, boolean>
}

const updateSeparatedEnabledSnapshot = (snapshot = currentSeparatedEnabledSnapshot()) => {
  if (!selectedJobId.value) return
  const nextJobs = { ...(workspacePrefs.value.jobs || {}) }
  nextJobs[selectedJobId.value] = {
    ...(nextJobs[selectedJobId.value] || {}),
    separatedEnabledSnapshot: snapshot,
  }
  workspacePrefs.value = {
    ...workspacePrefs.value,
    jobs: nextJobs,
  }
}

const getPlaybackClockStem = () => {
  if (getTrack('original')?.enabled && audioRefs.has('original')) {
    return 'original'
  }
  return SEPARATED_STEMS.find((stem) => getTrack(stem)?.enabled && audioRefs.has(stem)) || 'original'
}

const getTrackFilename = (stem: MusicStem) =>
  audioFiles.value.find((file) => file.stem === stem)?.filename || ''

const getTrackFile = (stem: MusicStem) => audioFiles.value.find((file) => file.stem === stem)

const getWaveformPeaks = (stem: MusicStem) => {
  const file = getTrackFile(stem)
  if (!file) return []
  return waveformCache[file.url] || (waveformLoading[file.url] ? Array.from({ length: 96 }, () => 0.08) : [])
}

const setAudioTime = (audio: HTMLAudioElement, time: number) => {
  try {
    audio.currentTime = Math.max(0, time)
  } catch (error) {
    console.warn('Failed to sync audio time', error)
  }
}

const setAudioRef = (stem: MusicStem, el: Element | null) => {
  if (el == null) {
    audioRefs.delete(stem)
    return
  }
  if (el instanceof HTMLAudioElement) {
    const existing = audioRefs.get(stem)
    if (existing === el) return
    audioRefs.set(stem, el)
    applyTrackVolume(stem)
    if (currentTime.value > 0) {
      setAudioTime(el, currentTime.value)
    }
  }
}

const loadStoredWorkspace = () => {
  try {
    const raw = window.localStorage.getItem(WORKSPACE_STORAGE_KEY)
    if (!raw) return {}
    const payload = JSON.parse(raw)
    if (!payload || typeof payload !== 'object') return {}
    return payload as StoredWorkspace
  } catch (error) {
    console.warn('Failed to load music workspace preferences', error)
    return {}
  }
}

const loadCollapsedInstrumentRoles = () => {
  try {
    const raw = window.localStorage.getItem(INSTRUMENT_ROLE_COLLAPSE_STORAGE_KEY)
    if (!raw) return [...DEFAULT_COLLAPSED_INSTRUMENT_ROLES]
    const payload = JSON.parse(raw)
    if (!Array.isArray(payload)) return [...DEFAULT_COLLAPSED_INSTRUMENT_ROLES]
    return payload.filter((role): role is string => typeof role === 'string')
  } catch (error) {
    console.warn('Failed to load instrument role collapse preferences', error)
    return [...DEFAULT_COLLAPSED_INSTRUMENT_ROLES]
  }
}

const persistCollapsedInstrumentRoles = () => {
  window.localStorage.setItem(
    INSTRUMENT_ROLE_COLLAPSE_STORAGE_KEY,
    JSON.stringify(collapsedInstrumentRoles.value),
  )
}

const loadExpandedInstrumentCellGroups = () => {
  try {
    const raw = window.localStorage.getItem(INSTRUMENT_CELL_GROUP_COLLAPSE_STORAGE_KEY)
    if (!raw) return []
    const payload = JSON.parse(raw)
    if (!Array.isArray(payload)) return []
    return payload.filter((group): group is string => typeof group === 'string')
  } catch (error) {
    console.warn('Failed to load instrument cell group preferences', error)
    return []
  }
}

const persistExpandedInstrumentCellGroups = () => {
  window.localStorage.setItem(
    INSTRUMENT_CELL_GROUP_COLLAPSE_STORAGE_KEY,
    JSON.stringify(expandedInstrumentCellGroups.value),
  )
}

const isInstrumentRoleCollapsed = (role: string) => collapsedInstrumentRoles.value.includes(role)

const toggleInstrumentRole = (role: string) => {
  if (isInstrumentRoleCollapsed(role)) {
    collapsedInstrumentRoles.value = collapsedInstrumentRoles.value.filter((item) => item !== role)
  } else {
    collapsedInstrumentRoles.value = [...collapsedInstrumentRoles.value, role]
  }
  persistCollapsedInstrumentRoles()
}

const isInstrumentCellGroupExpanded = (key: string) => expandedInstrumentCellGroups.value.includes(key)

const toggleInstrumentCellGroup = (key: string) => {
  if (isInstrumentCellGroupExpanded(key)) {
    expandedInstrumentCellGroups.value = expandedInstrumentCellGroups.value.filter((item) => item !== key)
  } else {
    expandedInstrumentCellGroups.value = [...expandedInstrumentCellGroups.value, key]
  }
  persistExpandedInstrumentCellGroups()
}

const serializeCurrentJobWorkspace = (): StoredJobWorkspace => ({
  currentTime: Number.isFinite(currentTime.value) ? currentTime.value : 0,
  scoreKind: selectedScoreKind.value,
  separatedEnabledSnapshot:
    getCurrentJobPrefs()?.separatedEnabledSnapshot || currentSeparatedEnabledSnapshot(),
  tracks: Object.fromEntries(
    tracks.map((track) => [
      track.key,
      {
        enabled: track.enabled,
        volume: track.volume,
      },
    ]),
  ) as Record<MusicStem, StoredTrackPreference>,
})

const persistWorkspaceNow = () => {
  if (isRestoringWorkspace.value || !selectedJobId.value) return
  const nextPrefs: StoredWorkspace = {
    selectedJobId: selectedJobId.value,
    selectedEngine: selectedEngine.value,
    jobs: {
      ...(workspacePrefs.value.jobs || {}),
      [selectedJobId.value]: serializeCurrentJobWorkspace(),
    },
  }
  workspacePrefs.value = nextPrefs
  window.localStorage.setItem(WORKSPACE_STORAGE_KEY, JSON.stringify(nextPrefs))
}

const schedulePersistWorkspace = () => {
  if (isRestoringWorkspace.value || !selectedJobId.value) return
  if (persistTimer.value != null) {
    window.clearTimeout(persistTimer.value)
  }
  persistTimer.value = window.setTimeout(() => {
    persistTimer.value = null
    persistWorkspaceNow()
  }, 250)
}

const applyStoredJobWorkspace = (jobId: string) => {
  const jobPrefs = workspacePrefs.value.jobs?.[jobId]
  for (const track of tracks) {
    const defaultTrack = DEFAULT_TRACKS.find((item) => item.key === track.key)
    if (!track) continue
    const storedTrack = jobPrefs?.tracks?.[track.key]
    track.enabled = typeof storedTrack?.enabled === 'boolean' ? storedTrack.enabled : defaultTrack?.enabled ?? track.enabled
    track.volume = typeof storedTrack?.volume === 'number' ? storedTrack.volume : defaultTrack?.volume ?? track.volume
  }
  if (getTrack('original')?.enabled) {
    for (const separatedTrack of getSeparatedTracks()) {
      separatedTrack.enabled = false
    }
  } else {
    updateSeparatedEnabledSnapshot(currentSeparatedEnabledSnapshot())
  }
  if (
    jobPrefs?.scoreKind === 'piano_solo_score' ||
    jobPrefs?.scoreKind === 'piano_stem_transcription' ||
    jobPrefs?.scoreKind === 'piano_stem_transcription_clean' ||
    jobPrefs?.scoreKind === 'melody_skeleton'
  ) {
    selectedScoreKind.value = jobPrefs.scoreKind
  } else {
    selectedScoreKind.value = 'melody_skeleton'
  }
  currentTime.value = Math.max(0, jobPrefs?.currentTime || 0)
}

const resetPlayback = (options: { resetTime?: boolean } = {}) => {
  pauseAll()
  if (options.resetTime !== false) {
    currentTime.value = 0
  }
  duration.value = 0
  audioRefs.clear()
}

const selectJob = async (job: MusicJob) => {
  persistWorkspaceNow()
  stopScorePlayback()
  scoreCurrentTime.value = 0
  creativeBrief.value = null
  creativePromptRecords.value = []
  selectedCreativeStyleKey.value = 'base'
  isRestoringWorkspace.value = true
  selectedJobId.value = job.job_id
  resetPlayback({ resetTime: false })
  ensureTracksForAudioFiles()
  applyStoredJobWorkspace(job.job_id)
  await loadScore(job.job_id)
  await nextTick()
  for (const track of tracks) {
    applyTrackVolume(track.key)
  }
  for (const audio of audioRefs.values()) {
    if (currentTime.value > 0) {
      setAudioTime(audio, currentTime.value)
    }
  }
  isRestoringWorkspace.value = false
  persistWorkspaceNow()
}

const loadCreativeBrief = async () => {
  if (!activeJob.value || creativeBriefLoading.value) return
  creativeBriefLoading.value = true
  try {
    creativeBrief.value = await getMusicJobCreativeBrief(activeJob.value.job_id)
    selectedCreativeStyleKey.value = creativeBrief.value.style_directions[0]?.key || 'base'
    selectedCreativePresetKey.value = creativeBrief.value.style_presets?.[0]?.key || ''
    await loadCreativePromptRecords()
  } catch (error) {
    console.error(error)
    ElMessage.error('生成音乐描述失败')
  } finally {
    creativeBriefLoading.value = false
  }
}

const loadCreativePromptRecords = async () => {
  if (!activeJob.value) {
    creativePromptRecords.value = []
    return
  }
  try {
    const payload = await listMusicJobCreativePrompts(activeJob.value.job_id)
    creativePromptRecords.value = payload.records
  } catch (error) {
    console.error(error)
    creativePromptRecords.value = []
  }
}

const saveCreativePromptVersion = async (name: string, promptZh: string, promptEn: string | null, source: string) => {
  if (!activeJob.value || !creativeBrief.value || creativePromptSaving.value) return
  creativePromptSaving.value = true
  try {
    const record = await saveMusicJobCreativePrompt(activeJob.value.job_id, {
      name,
      prompt_zh: promptZh,
      prompt_en: promptEn,
      source,
      audio_features: creativeBrief.value.audio_features,
    })
    creativePromptRecords.value = [record, ...creativePromptRecords.value.filter((item) => item.id !== record.id)]
    ElMessage.success('已保存提示词版本')
  } catch (error) {
    console.error(error)
    ElMessage.error('保存提示词失败')
  } finally {
    creativePromptSaving.value = false
  }
}

const saveCurrentCreativePrompt = async () => {
  const style = selectedCreativeStyle.value
  await saveCreativePromptVersion(style?.name || '当前提示词', currentCreativePromptZh.value, currentCreativePromptEn.value, style?.key || 'brief')
}

const recipePrompt = (recipe: CreativeRecipe, platform: GenerationPlatform) => {
  if (platform === 'udio') {
    return String(recipe.platform_prompts.udio_prompt || recipe.platform_prompts.suno_prompt || recipe.hook || '').trim()
  }
  return String(recipe.platform_prompts.suno_prompt || recipe.hook || '').trim()
}

const recipeStyle = (recipe: CreativeRecipe) => String(recipe.platform_prompts.suno_style || '').trim()

const presetPrompt = (preset: CreativeStylePreset, platform: GenerationPlatform) => {
  if (platform === 'udio') return String(preset.udio_prompt || preset.suno_prompt || '').trim()
  return String(preset.suno_prompt || '').trim()
}

const titleIdeasText = () => {
  const ideas = creativeBrief.value?.suno_fields?.title_ideas
  if (!Array.isArray(ideas)) return ''
  return ideas.map((item) => String(item || '').trim()).filter(Boolean).join(' / ')
}

const presetPackageText = (preset: CreativeStylePreset, platform: GenerationPlatform) => {
  const prompt = presetPrompt(preset, platform)
  const rows = [
    `平台：${platform.toUpperCase()}`,
    `路线：${preset.name}`,
    titleIdeasText() ? `标题候选：${titleIdeasText()}` : '',
    platform === 'suno' && preset.suno_style ? `Style：${preset.suno_style}` : '',
    prompt ? `Prompt：${prompt}` : '',
    preset.negative ? `Negative：${preset.negative}` : '',
    preset.listen_check?.length ? `回听检查：${preset.listen_check.join(' / ')}` : '',
    preset.copy_order?.length ? `投放顺序：${preset.copy_order.join(' -> ')}` : '',
  ]
  return rows.filter(Boolean).join('\n')
}

const recipePackageText = (recipe: CreativeRecipe, platform: GenerationPlatform) => {
  const prompt = recipePrompt(recipe, platform)
  const style = recipeStyle(recipe)
  const rows = [
    `平台：${platform.toUpperCase()}`,
    `方案：${recipe.title}`,
    titleIdeasText() ? `标题候选：${titleIdeasText()}` : '',
    platform === 'suno' && style ? `Style：${style}` : '',
    prompt ? `Prompt：${prompt}` : '',
    recipe.platform_prompts.negative ? `Negative：${recipe.platform_prompts.negative}` : '',
    recipe.listen_first?.length ? `先听：${recipe.listen_first.join(' / ')}` : '',
    recipe.arrangement_moves?.length ? `编曲动作：${recipe.arrangement_moves.join(' / ')}` : '',
  ]
  return rows.filter(Boolean).join('\n')
}

const saveRecipePromptVersion = async (recipe: CreativeRecipe, platform: GenerationPlatform) => {
  const prompt = recipePackageText(recipe, platform)
  if (!prompt) return
  const style = platform === 'suno' ? recipeStyle(recipe) : null
  await saveCreativePromptVersion(
    `${recipe.title} · ${platform.toUpperCase()}`,
    prompt,
    style,
    `recipe:${recipe.key}:${platform}`,
  )
}

const savePresetPromptVersion = async (preset: CreativeStylePreset, platform: GenerationPlatform) => {
  const prompt = presetPackageText(preset, platform)
  if (!prompt) return
  const style = platform === 'suno' ? String(preset.suno_style || '').trim() : null
  await saveCreativePromptVersion(
    `${preset.name} · ${platform.toUpperCase()}`,
    prompt,
    style,
    `preset:${preset.key}:${platform}`,
  )
}

const openGenerationPlatform = (platform: GenerationPlatform) => {
  window.open(GENERATION_PLATFORM_URLS[platform], '_blank', 'noreferrer')
}

const copyAndOpenRecipe = async (recipe: CreativeRecipe, platform: GenerationPlatform) => {
  const prompt = recipePackageText(recipe, platform)
  if (!prompt) return
  await copyPrompt(prompt)
  openGenerationPlatform(platform)
}

const copyAndOpenPreset = async (preset: CreativeStylePreset, platform: GenerationPlatform) => {
  const prompt = presetPackageText(preset, platform)
  if (!prompt) return
  await copyPrompt(prompt)
  openGenerationPlatform(platform)
}

const copyPrompt = async (text: string) => {
  const value = String(text || '').trim()
  if (!value) return
  try {
    try {
      if (!navigator.clipboard?.writeText) {
        throw new Error('Clipboard API unavailable')
      }
      await navigator.clipboard.writeText(value)
    } catch {
      const textarea = document.createElement('textarea')
      textarea.value = value
      textarea.style.position = 'fixed'
      textarea.style.left = '-9999px'
      textarea.style.top = '0'
      document.body.appendChild(textarea)
      textarea.focus()
      textarea.select()
      const ok = document.execCommand('copy')
      textarea.remove()
      if (!ok) throw new Error('Fallback copy failed')
    }
    manualCopyText.value = ''
    ElMessage.success('已复制提示词')
  } catch (error) {
    console.error(error)
    manualCopyText.value = value
    ElMessage.warning('浏览器限制自动复制，已展开文本')
  }
}

const renameHistoryJob = async (job: MusicJob) => {
  try {
    const { value } = await ElMessageBox.prompt('给这条解析历史起一个更容易识别的名字', '重命名解析历史', {
      confirmButtonText: '保存',
      cancelButtonText: '取消',
      inputValue: job.filename,
      inputPattern: /\S/,
      inputErrorMessage: '名称不能为空',
    })
    const nextName = String(value || '').trim()
    if (!nextName || nextName === job.filename) return
    const updated = await updateMusicJob(job.job_id, { filename: nextName })
    const index = jobs.value.findIndex((item) => item.job_id === updated.job_id)
    if (index >= 0) {
      jobs.value.splice(index, 1, updated)
    }
    ElMessage.success('已重命名')
  } catch (error: any) {
    if (error === 'cancel' || error === 'close') return
    console.error(error)
    ElMessage.error(error?.response?.data?.detail || '重命名失败')
  }
}

const scoreFile = (key: string) => scoreInfo.value?.files.find((file) => file.key === key) || null

const scoreFileUrl = (key: string) => scoreFile(key)?.url || ''

const setOnlyPianoTrackEnabled = () => {
  const pianoTrack = getTrack('piano')
  if (!pianoTrack || !getTrackFile('piano')) return
  pauseAll()
  stopScorePlayback()
  for (const track of tracks) {
    track.enabled = track.key === 'piano'
  }
  updateSeparatedEnabledSnapshot(currentSeparatedEnabledSnapshot())
  applyAllTrackVolumes()
  schedulePersistWorkspace()
}

const loadSelectedScoreNotes = async () => {
  stopScorePlayback()
  scoreCurrentTime.value = 0
  scoreNotes.value = []
  scoreInfo.value = scoreInfos.value.find((score) => score.kind === selectedScoreKind.value) || null
  if (!scoreInfo.value) return
  const notesUrl = scoreInfo.value.files.find((file) => file.key === 'notes')?.url
  if (!notesUrl) return
  const response = await fetch(notesUrl)
  if (!response.ok) {
    throw new Error(`Score notes request failed: ${response.status}`)
  }
  const payload = await response.json()
  const tempo = Number(payload?.tempo_bpm || scoreInfo.value.tempo_bpm || 80)
  const rawNotes = Array.isArray(payload?.notes) ? payload.notes : []
  scoreNotes.value = rawNotes
    .map((raw: any, index: number): ScoreNote | null => {
      const note = Number(raw?.note)
      const beat = Number(raw?.beat)
      const durationBeat = Number(raw?.dur)
      const rawStart = Number(raw?.start)
      const rawEnd = Number(raw?.end)
      const hasSecondTiming =
        Number.isFinite(rawStart) && Number.isFinite(rawEnd) && rawEnd > rawStart
      const hasBeatTiming =
        Number.isFinite(beat) && Number.isFinite(durationBeat) && durationBeat > 0
      if (!Number.isFinite(note) || (!hasSecondTiming && !hasBeatTiming)) {
        return null
      }
      const start = hasSecondTiming ? rawStart : (beat * 60) / tempo
      const end = hasSecondTiming ? rawEnd : ((beat + Math.max(0.05, durationBeat)) * 60) / tempo
      return {
        id: `${index}:${note}:${start}`,
        start,
        end,
        note,
        velocity: Number(raw?.velocity || 72),
        hand: String(raw?.hand || ''),
      }
    })
    .filter((note): note is ScoreNote => Boolean(note))
    .sort((a, b) => a.start - b.start)
}

const loadScore = async (jobId: string) => {
  scoreInfos.value = []
  scoreInfo.value = null
  scoreNotes.value = []
  if (!jobId) return
  scoreLoading.value = true
  try {
    const payload = await listMusicJobScores(jobId)
    scoreInfos.value = payload.scores
    if (!scoreInfos.value.some((score) => score.kind === selectedScoreKind.value)) {
      selectedScoreKind.value = scoreInfos.value.some((score) => score.kind === 'melody_skeleton')
        ? 'melody_skeleton'
        : scoreInfos.value.some((score) => score.kind === 'piano_stem_transcription_clean')
          ? 'piano_stem_transcription_clean'
          : scoreInfos.value.some((score) => score.kind === 'piano_stem_transcription')
            ? 'piano_stem_transcription'
            : 'piano_solo_score'
    }
    await loadSelectedScoreNotes()
  } catch (error: any) {
    if (error?.response?.status !== 404) {
      console.error(error)
    }
  } finally {
    scoreLoading.value = false
  }
}

const handleScoreKindChange = async () => {
  if (isPianoStemScoreKind(selectedScoreKind.value)) {
    setOnlyPianoTrackEnabled()
  }
  scoreLoading.value = true
  try {
    await loadSelectedScoreNotes()
  } catch (error) {
    console.error(error)
    ElMessage.error('加载谱面失败')
  } finally {
    scoreLoading.value = false
    schedulePersistWorkspace()
  }
}

const loadJobs = async (preferredJobId = '') => {
  const payload = await listMusicJobs()
  jobs.value = payload.jobs
  const storedJobId = workspacePrefs.value.selectedJobId || ''
  const nextJob =
    jobs.value.find((job) => job.job_id === preferredJobId) ||
    jobs.value.find((job) => job.job_id === storedJobId) ||
    jobs.value[0]
  if (nextJob) {
    await selectJob(nextJob)
    await resumeJobPolling(nextJob)
  }
}

const resumeJobPolling = async (job: MusicJob) => {
  if (!job.task_id || (job.status !== 'queued' && job.status !== 'running')) return
  if (task.value?.task_id === job.task_id && task.value.running) return
  try {
    task.value = await getMusicSeparationTask(job.task_id)
    if (task.value.running) {
      startPolling()
      return
    }
    await loadJobs(job.job_id)
  } catch (error) {
    console.error(error)
  }
}

const handleFileChange = (event: Event) => {
  const input = event.target as HTMLInputElement
  selectedFile.value = input.files?.[0] || null
}

const loadMultitrackSources = async () => {
  if (multitrackSources.value.length) return
  try {
    const payload = await listMultitrackLibrary()
    multitrackSources.value = payload.sources
    if (!selectedMultitrackWorkKey.value) {
      const firstSource = payload.sources[0]
      const firstWork = firstSource?.featured_works?.[0]
      if (firstSource && firstWork) {
        selectedMultitrackWorkKey.value = `${firstSource.id}:0:${firstWork.title}`
        selectedMultitrackSourceId.value = firstSource.id
      }
    }
  } catch (error) {
    console.error(error)
    ElMessage.error('加载真实分轨素材来源失败')
  }
}

const activateMultitrackMode = async () => {
  commandMode.value = 'multitrack'
  await loadMultitrackSources()
}

const selectMultitrackWork = (key: string) => {
  const work = featuredMultitrackWorks.value.find((item) => item.key === key)
  if (!work) return
  selectedMultitrackWorkKey.value = key
  selectedMultitrackSourceId.value = work.sourceId
}

const openSelectedMultitrackSource = () => {
  const work = selectedMultitrackWork.value
  if (!work?.sourceUrl) return
  selectedMultitrackSourceId.value = work.sourceId
  window.open(work.sourceUrl, '_blank', 'noreferrer')
}

const handleMultitrackZipChange = (event: Event) => {
  const input = event.target as HTMLInputElement
  multitrackZipFile.value = input.files?.[0] || null
}

const importSelectedMultitrackZip = async () => {
  const url = multitrackZipUrl.value.trim()
  if ((!multitrackZipFile.value && !url) || multitrackImporting.value) return
  stopPolling()
  persistWorkspaceNow()
  resetPlayback()
  stopScorePlayback()
  multitrackImporting.value = true
  try {
    const job = url
      ? await importMultitrackZipUrl(url, selectedMultitrackSourceId.value)
      : await importMultitrackZip(multitrackZipFile.value as File, selectedMultitrackSourceId.value)
    multitrackZipFile.value = null
    multitrackZipUrl.value = ''
    await loadJobs(job.job_id)
    ElMessage.success(url ? '真实分轨 URL 已导入' : '真实分轨 ZIP 已导入')
  } catch (error: any) {
    console.error(error)
    ElMessage.error(error?.response?.data?.detail || '导入真实分轨 ZIP 失败')
  } finally {
    multitrackImporting.value = false
  }
}

const clearRecordedHummingUrl = () => {
  if (recordedHummingUrl.value) {
    URL.revokeObjectURL(recordedHummingUrl.value)
    recordedHummingUrl.value = ''
  }
}

const handleHummingFileChange = (event: Event) => {
  const input = event.target as HTMLInputElement
  clearRecordedHummingUrl()
  hummingFile.value = input.files?.[0] || null
}

const stopRecordingStream = () => {
  recordingStream.value?.getTracks().forEach((track) => track.stop())
  recordingStream.value = null
}

const startRecording = async () => {
  if (!canRecordHumming.value || isRecording.value) return
  recordingStarting.value = true
  try {
    clearRecordedHummingUrl()
    recordingChunks.splice(0, recordingChunks.length)
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    recordingStream.value = stream
    const recorder = new MediaRecorder(stream)
    mediaRecorder.value = recorder
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) {
        recordingChunks.push(event.data)
      }
    }
    recorder.onstop = () => {
      const mimeType = recorder.mimeType || 'audio/webm'
      const blob = new Blob(recordingChunks, { type: mimeType })
      const extension = mimeType.includes('mp4') ? 'm4a' : 'webm'
      const file = new File([blob], `humming-${Date.now()}.${extension}`, { type: mimeType })
      hummingFile.value = file
      recordedHummingUrl.value = URL.createObjectURL(blob)
      stopRecordingStream()
      mediaRecorder.value = null
      isRecording.value = false
    }
    recorder.start()
    isRecording.value = true
  } catch (error) {
    console.error(error)
    ElMessage.error('无法访问麦克风')
    stopRecordingStream()
  } finally {
    recordingStarting.value = false
  }
}

const stopRecording = () => {
  if (!mediaRecorder.value || mediaRecorder.value.state === 'inactive') return
  mediaRecorder.value.stop()
}

const toggleRecording = async () => {
  if (isRecording.value) {
    stopRecording()
    return
  }
  await startRecording()
}

const startSeparation = async () => {
  if (!selectedFile.value) return
  stopPolling()
  persistWorkspaceNow()
  resetPlayback()
  try {
    task.value = await startMusicSeparation(selectedFile.value, selectedEngine.value)
    if (taskJobId.value) {
      selectedJobId.value = taskJobId.value
    }
    await loadJobs(taskJobId.value)
    startPolling()
  } catch (error) {
    console.error(error)
    ElMessage.error('启动分轨失败')
  }
}

const transcribeHumming = async () => {
  if (!hummingFile.value) return
  stopPolling()
  persistWorkspaceNow()
  resetPlayback()
  stopScorePlayback()
  selectedScoreKind.value = 'melody_skeleton'
  try {
    task.value = await startHummingTranscription(hummingFile.value, hummingTempoBpm.value, hummingBeatsPerBar.value)
    if (taskJobId.value) {
      selectedJobId.value = taskJobId.value
    }
    await loadJobs(taskJobId.value)
    startPolling()
  } catch (error) {
    console.error(error)
    ElMessage.error('启动哼唱转谱失败')
  }
}

const clearCurrentJobWorkspacePrefs = (jobId: string) => {
  const nextJobs = { ...(workspacePrefs.value.jobs || {}) }
  delete nextJobs[jobId]
  workspacePrefs.value = {
    ...workspacePrefs.value,
    selectedJobId: jobId,
    jobs: nextJobs,
  }
  window.localStorage.setItem(WORKSPACE_STORAGE_KEY, JSON.stringify(workspacePrefs.value))
}

const reparseActiveJob = async () => {
  if (!activeJob.value || task.value?.running) return
  stopPolling()
  clearCurrentJobWorkspacePrefs(activeJob.value.job_id)
  resetPlayback()
  stopScorePlayback()
  scoreInfos.value = []
  scoreInfo.value = null
  scoreNotes.value = []
  scoreCurrentTime.value = 0
  try {
    task.value = await rerunMusicJob(activeJob.value.job_id, selectedEngine.value)
    if (taskJobId.value) {
      selectedJobId.value = taskJobId.value
    }
    await loadJobs(taskJobId.value)
    startPolling()
  } catch (error) {
    console.error(error)
    ElMessage.error('启动重新解析失败')
  }
}

const loadInstrumentRegistry = async () => {
  if (instrumentRegistry.value || instrumentLoading.value) return
  instrumentLoading.value = true
  try {
    instrumentRegistry.value = await getMusicInstrumentRegistry()
  } catch (error) {
    console.error(error)
    ElMessage.error('加载乐器资料表失败')
  } finally {
    instrumentLoading.value = false
  }
}

const handleViewChange = async () => {
  if (activeView.value === 'instruments') {
    await loadInstrumentRegistry()
  }
}

const instrumentDescription = (row: MusicInstrumentRecord) => {
  const description = row.musicbrainz?.description || row.musicbrainz?.comment || ''
  if (description) {
    return description
  }
  const parts = [
    topClassSummary(row) ? `发声方式：${topClassSummary(row)}` : '',
    roleSummary(row) ? `常见用途：${roleSummary(row)}` : '',
    musescoreSummary(row) ? `记谱归类：${musescoreSummary(row)}` : '',
  ].filter(Boolean)
  return parts.length ? parts.join('；') : '暂无更详细说明。'
}

const selectInstrument = (instrument: MusicInstrumentRecord) => {
  selectedInstrument.value = instrument
}

const startPolling = () => {
  if (!task.value) return
  stopPolling()
  pollTimer.value = window.setInterval(async () => {
    if (!task.value) return
    try {
      task.value = await getMusicSeparationTask(task.value.task_id)
      if (!task.value.running) {
        stopPolling()
        await loadJobs(taskJobId.value)
        if (task.value.status === 'completed') {
          ElMessage.success(task.value.metadata.input_kind === 'humming' ? '哼唱转谱完成' : '音轨分离完成')
        }
      }
    } catch (error) {
      console.error(error)
      stopPolling()
      ElMessage.error('查询分轨状态失败')
    }
  }, 1500)
}

const stopPolling = () => {
  if (pollTimer.value != null) {
    window.clearInterval(pollTimer.value)
    pollTimer.value = null
  }
}

const applyTrackVolume = (stem: MusicStem) => {
  const audio = audioRefs.get(stem)
  const track = getTrack(stem)
  if (!audio || !track) return
  audio.volume = track.volume
  audio.muted = !track.enabled || track.volume <= 0
  schedulePersistWorkspace()
}

const applyAllTrackVolumes = () => {
  for (const track of tracks) {
    applyTrackVolume(track.key)
  }
}

const enabledAudios = () =>
  tracks
    .filter((track) => track.enabled)
    .map((track) => audioRefs.get(track.key))
    .filter((audio): audio is HTMLAudioElement => Boolean(audio))

const togglePlayback = async () => {
  if (isPlaying.value) {
    pauseAll()
    return
  }
  await playAll()
}

const playAll = async () => {
  const audios = enabledAudios()
  if (!audios.length) return
  stopScorePlayback()
  for (const audio of audios) {
    setAudioTime(audio, currentTime.value)
  }
  try {
    await Promise.all(audios.map((audio) => audio.play()))
    isPlaying.value = true
  } catch (error) {
    console.error(error)
    ElMessage.error('播放失败，浏览器可能拦截了音频播放')
  }
}

const pauseAll = () => {
  for (const audio of audioRefs.values()) {
    audio.pause()
  }
  isPlaying.value = false
}

const getScoreAudioContext = () => {
  if (scoreAudioContext.value) return scoreAudioContext.value
  const AudioContextCtor = window.AudioContext || (window as any).webkitAudioContext
  scoreAudioContext.value = new AudioContextCtor()
  return scoreAudioContext.value
}

const scoreNoteFrequency = (note: number) => 440 * 2 ** ((note - 69) / 12)

const playScoreNote = (note: ScoreNote, when: number) => {
  const context = getScoreAudioContext()
  const oscillator = context.createOscillator()
  const gain = context.createGain()
  const durationSeconds = Math.max(0.08, note.end - note.start)
  const velocity = Math.min(1, Math.max(0.12, note.velocity / 127))

  oscillator.type = 'triangle'
  oscillator.frequency.setValueAtTime(scoreNoteFrequency(note.note), when)
  gain.gain.setValueAtTime(0.0001, when)
  gain.gain.exponentialRampToValueAtTime(0.18 * velocity, when + 0.018)
  gain.gain.exponentialRampToValueAtTime(0.06 * velocity, when + Math.min(durationSeconds * 0.55, 0.8))
  gain.gain.exponentialRampToValueAtTime(0.0001, when + durationSeconds + 0.18)
  oscillator.connect(gain)
  gain.connect(context.destination)
  oscillator.start(when)
  oscillator.stop(when + durationSeconds + 0.22)
}

const scheduleScoreNotes = () => {
  if (!isScorePlaying.value) return
  const context = getScoreAudioContext()
  const now = scoreCurrentTime.value
  const scheduleUntil = now + 0.35
  for (const note of scoreNotes.value) {
    if (note.start < now - 0.02) continue
    if (note.start > scheduleUntil) break
    if (scheduledScoreNotes.has(note.id)) continue
    scheduledScoreNotes.add(note.id)
    playScoreNote(note, context.currentTime + Math.max(0, note.start - now))
  }
}

const tickScorePlayback = () => {
  if (!isScorePlaying.value) return
  scoreCurrentTime.value = scorePlaybackBaseTime.value + (performance.now() - scorePlaybackStartedAt.value) / 1000
  if (scoreCurrentTime.value >= scoreDuration.value) {
    stopScorePlayback()
    scoreCurrentTime.value = 0
    return
  }
  scheduleScoreNotes()
  scoreAnimationFrame.value = window.requestAnimationFrame(tickScorePlayback)
}

const playScore = async () => {
  if (!scoreNotes.value.length) return
  pauseAll()
  const context = getScoreAudioContext()
  if (context.state === 'suspended') {
    await context.resume()
  }
  scheduledScoreNotes.clear()
  scorePlaybackBaseTime.value = Math.min(scoreCurrentTime.value, Math.max(0, scoreDuration.value - 0.05))
  scorePlaybackStartedAt.value = performance.now()
  isScorePlaying.value = true
  tickScorePlayback()
}

const stopScorePlayback = () => {
  isScorePlaying.value = false
  scheduledScoreNotes.clear()
  if (scoreAnimationFrame.value != null) {
    window.cancelAnimationFrame(scoreAnimationFrame.value)
    scoreAnimationFrame.value = null
  }
}

const toggleScorePlayback = async () => {
  if (isScorePlaying.value) {
    stopScorePlayback()
    return
  }
  try {
    await playScore()
  } catch (error) {
    console.error(error)
    ElMessage.error('谱面演奏失败，浏览器可能拦截了音频播放')
  }
}

const seekTo = (value: number | number[]) => {
  const nextTime = Array.isArray(value) ? value[0] : value
  currentTime.value = nextTime
  for (const audio of audioRefs.values()) {
    setAudioTime(audio, nextTime)
  }
  schedulePersistWorkspace()
}

const seekScoreTo = (value: number | number[]) => {
  const nextTime = Array.isArray(value) ? value[0] : value
  scoreCurrentTime.value = Math.min(scoreDuration.value || 0, Math.max(0, nextTime))
  scheduledScoreNotes.clear()
  if (isScorePlaying.value) {
    scorePlaybackBaseTime.value = scoreCurrentTime.value
    scorePlaybackStartedAt.value = performance.now()
  }
}

const handleWaveformClick = (event: MouseEvent, stem: MusicStem) => {
  if (!duration.value || !getTrackFile(stem)) return
  const target = event.currentTarget
  if (!(target instanceof HTMLElement)) return
  const rect = target.getBoundingClientRect()
  if (rect.width <= 0) return
  const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width))
  seekTo(ratio * duration.value)
}

const handleTrackToggle = async (stem: MusicStem) => {
  const track = getTrack(stem)
  if (!track) return

  if (stem === 'original') {
    if (track.enabled) {
      updateSeparatedEnabledSnapshot()
      for (const separatedTrack of getSeparatedTracks()) {
        separatedTrack.enabled = false
      }
    } else {
      const snapshot = getSeparatedEnabledSnapshot()
      for (const separatedTrack of getSeparatedTracks()) {
        separatedTrack.enabled = Boolean(snapshot[separatedTrack.key])
      }
    }
  } else if (track.enabled) {
    const originalTrack = getTrack('original')
    if (originalTrack) {
      originalTrack.enabled = false
    }
    updateSeparatedEnabledSnapshot()
  } else if (!getTrack('original')?.enabled) {
    updateSeparatedEnabledSnapshot()
  }

  applyAllTrackVolumes()
  schedulePersistWorkspace()

  for (const item of tracks) {
    const audio = audioRefs.get(item.key)
    if (!audio) continue
    setAudioTime(audio, currentTime.value)
    if (!item.enabled) {
      audio.pause()
    }
  }

  if (isPlaying.value) {
    const audios = enabledAudios()
    try {
      await Promise.all(audios.map((audio) => audio.play()))
    } catch (error) {
      console.error(error)
      ElMessage.error('播放失败，浏览器可能拦截了音频播放')
    }
  }
}

const handleMetadata = (stem: MusicStem) => {
  const audio = audioRefs.get(stem)
  if (audio && currentTime.value > 0) {
    setAudioTime(audio, Math.min(currentTime.value, audio.duration || currentTime.value))
  }
  if (stem === 'original' && audio?.duration && Number.isFinite(audio.duration)) {
    duration.value = audio.duration
    if (currentTime.value > audio.duration) {
      seekTo(audio.duration)
    }
  }
}

const handleTimeUpdate = (stem: MusicStem) => {
  if (stem !== getPlaybackClockStem()) return
  const audio = audioRefs.get(stem)
  if (!audio || Math.abs(audio.currentTime - currentTime.value) < 0.2) return
  currentTime.value = audio.currentTime
  schedulePersistWorkspace()
}

const handleEnded = (stem: MusicStem) => {
  if (stem === getPlaybackClockStem()) {
    pauseAll()
    currentTime.value = 0
    schedulePersistWorkspace()
  }
}

const statusText = (status: MusicJob['status']) => {
  if (status === 'completed') return '完成'
  if (status === 'failed') return '失败'
  if (status === 'running') return '运行中'
  return '排队中'
}

const formatDate = (timestamp: number | null | undefined) => {
  if (!timestamp) return ''
  return new Date(timestamp * 1000).toLocaleString()
}

const formatTime = (seconds: number) => {
  const safeSeconds = Number.isFinite(seconds) ? Math.max(0, seconds) : 0
  const minutes = Math.floor(safeSeconds / 60)
  const rest = Math.floor(safeSeconds % 60)
  return `${minutes}:${String(rest).padStart(2, '0')}`
}

const buildPeaks = (buffer: AudioBuffer, bucketCount: number) => {
  const channelCount = Math.max(1, buffer.numberOfChannels)
  const sampleCount = buffer.length
  const bucketSize = Math.max(1, Math.floor(sampleCount / bucketCount))
  const peaks: number[] = []

  for (let bucketIndex = 0; bucketIndex < bucketCount; bucketIndex += 1) {
    const start = bucketIndex * bucketSize
    const end = Math.min(sampleCount, start + bucketSize)
    let sum = 0
    let count = 0

    for (let channel = 0; channel < channelCount; channel += 1) {
      const data = buffer.getChannelData(channel)
      for (let index = start; index < end; index += 8) {
        const value = data[index] || 0
        sum += value * value
        count += 1
      }
    }

    peaks.push(count > 0 ? Math.sqrt(sum / count) : 0)
  }

  const maxPeak = Math.max(...peaks, 0.001)
  return peaks.map((peak) => Math.min(1, peak / maxPeak))
}

const loadWaveform = async (url: string) => {
  if (waveformCache[url] || waveformLoading[url]) return
  waveformLoading[url] = true
  try {
    const response = await fetch(url)
    if (!response.ok) {
      throw new Error(`Waveform request failed: ${response.status}`)
    }
    const arrayBuffer = await response.arrayBuffer()
    const AudioContextCtor = window.AudioContext || (window as any).webkitAudioContext
    const context = new AudioContextCtor()
    try {
      const decoded = await context.decodeAudioData(arrayBuffer.slice(0))
      waveformCache[url] = buildPeaks(decoded, 180)
    } finally {
      await context.close()
    }
  } catch (error) {
    console.error('Failed to build waveform', error)
    waveformCache[url] = []
  } finally {
    waveformLoading[url] = false
  }
}

watch(
  audioFiles,
  (files) => {
    ensureTracksForAudioFiles()
    for (const file of files) {
      void loadWaveform(file.url)
    }
  },
  { immediate: true },
)

watch(selectedEngine, () => {
  schedulePersistWorkspace()
})

watch(
  activeView,
  (view) => {
    if (view === 'instruments') {
      void loadInstrumentRegistry()
    }
  },
  { immediate: true },
)

watch(filteredInstruments, (items) => {
  if (!selectedInstrument.value) return
  if (!items.some((item) => item.id === selectedInstrument.value?.id)) {
    selectedInstrument.value = null
  }
})

onMounted(async () => {
  workspacePrefs.value = loadStoredWorkspace()
  collapsedInstrumentRoles.value = loadCollapsedInstrumentRoles()
  expandedInstrumentCellGroups.value = loadExpandedInstrumentCellGroups()
  if (route.query.mode === 'multitrack') {
    commandMode.value = 'multitrack'
    void loadMultitrackSources()
  } else if (route.query.mode === 'humming') {
    commandMode.value = 'humming'
  }
  if (workspacePrefs.value.selectedEngine === 'demucs' || workspacePrefs.value.selectedEngine === 'audio_separator_6s') {
    selectedEngine.value = workspacePrefs.value.selectedEngine
  }
  try {
    const [info] = await Promise.all([
      getMusicToolInfo().then((value) => {
        toolInfo.value = value
        return value
      }),
      loadJobs(),
    ])
    toolInfo.value = info
  } catch (error) {
    console.error(error)
  }
})

onBeforeUnmount(() => {
  persistWorkspaceNow()
  if (persistTimer.value != null) {
    window.clearTimeout(persistTimer.value)
    persistTimer.value = null
  }
  stopPolling()
  pauseAll()
  stopScorePlayback()
  if (isRecording.value) {
    stopRecording()
  }
  stopRecordingStream()
  clearRecordedHummingUrl()
  void scoreAudioContext.value?.close()
})
</script>

<style scoped>
.music-tools-page {
  --music-accent: #13a394;
  --music-accent-soft: #dff6f3;
  --music-blue: #2e82f0;
  --music-text: #101828;
  --music-muted: #687386;
  --music-line: #dbe3ee;
  --music-panel: #fff;
  --music-page: #eef3f8;
  --music-shadow: 0 16px 34px rgba(15, 23, 42, 0.08);

  padding: 18px 24px 28px;
  height: calc(100vh - 48px);
  min-height: 0;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow: hidden;
  background: var(--music-page);
}

.page-head,
.view-tabs,
.title-block,
.command-bar,
.command-mode,
.command-fields,
.transport,
.score-toolbar,
.score-left,
.score-actions,
.score-transport,
.stem-control-row,
.stem-heading {
  display: flex;
  align-items: center;
}

.page-head {
  justify-content: space-between;
  gap: 16px;
  width: min(100%, 1260px);
  min-height: 58px;
  padding: 16px 18px;
  border: 1px solid rgba(148, 163, 184, 0.22);
  border-radius: 14px 14px 10px 10px;
  box-sizing: border-box;
  background:
    linear-gradient(135deg, rgba(13, 25, 43, 0.98), rgba(20, 38, 63, 0.98) 58%, rgba(19, 69, 78, 0.92));
  box-shadow: var(--music-shadow);
  flex: 0 0 auto;
}

.page-head :deep(.el-tag) {
  border-color: rgba(134, 239, 172, 0.45);
  background: rgba(22, 163, 74, 0.14);
  color: #bbf7d0;
  font-weight: 650;
}

.title-block {
  min-width: 0;
  gap: 16px;
}

h1 {
  margin: 0;
  font-size: 25px;
  font-weight: 700;
  letter-spacing: 0;
  color: #f8fbff;
}

.view-tabs :deep(.el-radio-button__inner) {
  height: 32px;
  padding: 0 14px;
  display: inline-flex;
  align-items: center;
  border-color: rgba(148, 163, 184, 0.32);
  background: rgba(15, 23, 42, 0.42);
  color: #b8c5d8;
}

.view-tabs :deep(.el-radio-button__original-radio:checked + .el-radio-button__inner) {
  border-color: rgba(96, 165, 250, 0.7);
  background: #2e82f0;
  color: #fff;
  box-shadow: -1px 0 0 0 rgba(96, 165, 250, 0.7);
}

.command-bar {
  width: min(100%, 1260px);
  min-height: 58px;
  padding: 9px;
  flex: 0 0 auto;
  gap: 12px;
  border: 1px solid rgba(148, 163, 184, 0.22);
  border-radius: 10px 10px 14px 14px;
  background:
    linear-gradient(135deg, rgba(14, 28, 47, 0.98), rgba(20, 40, 65, 0.96) 62%, rgba(17, 83, 84, 0.9));
  box-shadow: var(--music-shadow);
  box-sizing: border-box;
}

.command-mode {
  padding: 4px;
  gap: 3px;
  border-radius: 9px;
  background: rgba(7, 13, 24, 0.42);
  flex: 0 0 auto;
}

.command-mode button {
  height: 32px;
  padding: 0 14px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: #a9b9ce;
  font-weight: 600;
  cursor: pointer;
}

.command-mode button.active {
  background: #ffffff;
  color: #0f172a;
  box-shadow: 0 8px 18px rgba(2, 8, 23, 0.22);
}

.command-fields {
  min-width: 0;
  flex: 1 1 auto;
  gap: 8px;
  flex-wrap: wrap;
}

.file-picker {
  min-width: 240px;
  max-width: 520px;
  height: 36px;
  padding: 0 13px;
  border: 1px solid rgba(203, 213, 225, 0.24);
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  color: #eef5ff;
  cursor: pointer;
  background: rgba(255, 255, 255, 0.1);
}

.command-file {
  flex: 1 1 280px;
}

.file-picker input {
  display: none;
}

.file-picker span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.engine-select {
  width: 120px;
}

.multitrack-source-select {
  width: 190px;
}

.multitrack-url-input {
  flex: 1 1 280px;
  min-width: 260px;
  max-width: 520px;
}

.command-fields :deep(.el-button) {
  height: 36px;
  border-radius: 8px;
  font-weight: 650;
}

.command-fields :deep(.el-input__wrapper),
.command-fields :deep(.el-select__wrapper) {
  min-height: 36px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.96);
  box-shadow: none;
}

.humming-picker {
  min-width: 240px;
}

.tempo-input {
  width: 112px;
}

.beats-input {
  width: 96px;
}

.recording-preview {
  display: block;
  width: 220px;
  height: 32px;
}

.multitrack-source-panel {
  width: min(100%, 1260px);
  padding: 12px;
  border: 1px solid var(--music-line);
  border-radius: 8px;
  background: #fff;
  box-sizing: border-box;
  display: grid;
  gap: 10px;
  flex: 0 0 auto;
}

.multitrack-source-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}

.multitrack-source-head strong {
  color: var(--music-text);
}

.multitrack-source-head span {
  color: var(--music-muted);
  font-size: 12px;
}

.multitrack-source-list {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.multitrack-source-card {
  min-width: 0;
  padding: 11px;
  border: 1px solid #d8e3f0;
  border-radius: 8px;
  display: grid;
  gap: 5px;
  color: inherit;
  text-decoration: none;
  background: #f8fbff;
}

.multitrack-source-card span {
  color: var(--music-blue);
  font-size: 12px;
  font-weight: 700;
}

.multitrack-source-card strong {
  color: var(--music-text);
}

.multitrack-source-card em {
  color: #0f766e;
  font-size: 12px;
  font-style: normal;
  line-height: 1.45;
}

.multitrack-source-card p {
  margin: 0;
  color: var(--music-muted);
  font-size: 12px;
  line-height: 1.55;
}

.multitrack-work-list {
  border: 1px solid #d8e3f0;
  border-radius: 8px;
  overflow: hidden;
  background: #fff;
}

.multitrack-work-title {
  padding: 8px 10px;
  border-bottom: 1px solid #e7eef7;
  color: var(--music-text);
  font-size: 13px;
  font-weight: 700;
  background: #fbfdff;
}

.multitrack-work-item {
  padding: 10px;
  border-bottom: 1px solid #edf2f8;
  display: grid;
  gap: 5px;
  text-align: left;
  cursor: pointer;
}

.multitrack-work-item:last-child {
  border-bottom: 0;
}

.multitrack-work-item.active {
  background: #eefaf8;
  box-shadow: inset 3px 0 0 var(--music-accent);
}

.multitrack-work-main {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}

.multitrack-work-main span {
  padding: 2px 7px;
  border-radius: 999px;
  color: #0f766e;
  font-size: 12px;
  font-style: normal;
  background: #e5f7f4;
}

.multitrack-work-main strong {
  color: var(--music-text);
}

.multitrack-work-main em {
  color: var(--music-muted);
  font-size: 12px;
  font-style: normal;
}

.multitrack-work-instruments {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.multitrack-work-instruments span {
  padding: 2px 6px;
  border-radius: 5px;
  color: #32608f;
  font-size: 12px;
  background: #eef6ff;
}

.multitrack-work-item p {
  margin: 0;
  color: #475467;
  font-size: 12px;
  line-height: 1.55;
}

.multitrack-work-item .style-bridge {
  color: #0f766e;
}

.multitrack-work-item a {
  justify-self: start;
  color: var(--music-blue);
  font-size: 12px;
  text-decoration: none;
}

.multitrack-study-panel {
  border: 1px solid #d8e3f0;
  border-radius: 8px;
  overflow: hidden;
  background: #fff;
}

.multitrack-study-head {
  padding: 10px 12px;
  border-bottom: 1px solid #e7eef7;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  background: #fbfdff;
}

.multitrack-study-head div:first-child {
  min-width: 0;
  display: grid;
  gap: 3px;
}

.multitrack-study-head span,
.multitrack-study-grid span {
  color: var(--music-muted);
  font-size: 12px;
}

.multitrack-study-head strong {
  color: var(--music-text);
}

.multitrack-study-actions {
  display: flex;
  gap: 7px;
  flex: 0 0 auto;
}

.multitrack-study-actions button {
  height: 28px;
  padding: 0 9px;
  border: 1px solid #cfe0f5;
  border-radius: 6px;
  color: var(--music-blue);
  font-size: 12px;
  font-weight: 650;
  background: #fff;
  cursor: pointer;
}

.multitrack-study-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  border-bottom: 1px solid #edf2f8;
}

.multitrack-study-grid div {
  min-width: 0;
  padding: 10px 12px;
  border-right: 1px solid #edf2f8;
  display: grid;
  gap: 5px;
}

.multitrack-study-grid div:nth-child(2n) {
  border-right: 0;
}

.multitrack-study-grid p {
  margin: 0;
  color: #344054;
  font-size: 12px;
  line-height: 1.55;
}

.multitrack-study-steps {
  padding: 9px 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}

.multitrack-study-steps span {
  padding: 4px 7px;
  border-radius: 6px;
  color: #32608f;
  font-size: 12px;
  background: #eef6ff;
}

.task-alert {
  width: min(100%, 1260px);
  flex: 0 0 auto;
}

.instrument-registry-panel {
  width: min(100%, 1220px);
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.registry-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.registry-summary {
  min-width: 260px;
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.registry-title {
  font-size: 18px;
  font-weight: 650;
  color: #111827;
}

.registry-meta,
.registry-source-row {
  color: #6b7280;
  font-size: 12px;
}

.registry-search {
  width: 260px;
}

.registry-select {
  width: 150px;
}

.registry-source-row {
  flex: 0 0 auto;
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}

.instrument-split-view {
  flex: 1 1 auto;
  min-height: 0;
}

.instrument-split-view :deep(.note-main-pane) {
  padding-bottom: 8px;
  box-sizing: border-box;
}

.instrument-split-view :deep(.note-editor-content) {
  padding: 10px 0 0;
}

.instrument-pivot-panel,
.instrument-detail-card {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
}

.instrument-pivot-panel {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.pivot-title-row,
.instrument-detail-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  border-bottom: 1px solid #eef0f3;
}

.pivot-title,
.instrument-detail-title {
  color: #111827;
  font-weight: 650;
}

.pivot-subtitle,
.instrument-detail-subtitle,
.pivot-count {
  color: #6b7280;
  font-size: 12px;
}

.pivot-scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
}

.pivot-table {
  width: 100%;
  min-width: 820px;
  border-collapse: separate;
  border-spacing: 0;
  table-layout: fixed;
}

.pivot-table th,
.pivot-table td {
  vertical-align: top;
  border-right: 1px solid #eef0f3;
  border-bottom: 1px solid #eef0f3;
}

.pivot-table th {
  padding: 10px;
  background: #f8fafc;
  color: #374151;
  font-weight: 650;
  text-align: left;
}

.pivot-table td {
  min-height: 72px;
  padding: 8px;
}

.pivot-table tr.is-collapsed td {
  min-height: 0;
  padding: 0 8px;
  background: #fbfcfe;
}

.pivot-role-head,
.pivot-role-cell {
  position: sticky;
  left: 0;
  z-index: 1;
  width: 96px;
}

.pivot-role-cell {
  background: #fff;
}

.pivot-table tr.is-collapsed .pivot-role-cell {
  background: #fbfcfe;
}

.role-toggle {
  width: 100%;
  padding: 0;
  border: 0;
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr) auto;
  align-items: center;
  gap: 6px;
  background: transparent;
  color: #111827;
  font: inherit;
  font-weight: 650;
  text-align: left;
  cursor: pointer;
}

.role-toggle-mark {
  width: 18px;
  height: 18px;
  border: 1px solid #d7dee8;
  border-radius: 4px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #667085;
  font-size: 12px;
  line-height: 1;
  background: #fff;
}

.role-toggle-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.role-toggle-count {
  color: #98a2b3;
  font-size: 12px;
  font-weight: 500;
}

.role-toggle:hover .role-toggle-mark {
  border-color: #409eff;
  color: #1d4ed8;
}

.pivot-cell-tree {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.cell-tree-group {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.cell-group-toggle {
  max-width: 100%;
  padding: 2px 0;
  border: 0;
  display: grid;
  grid-template-columns: 16px minmax(0, 1fr) auto;
  align-items: center;
  gap: 5px;
  background: transparent;
  color: #344054;
  font: inherit;
  font-size: 12px;
  font-weight: 650;
  text-align: left;
  cursor: pointer;
}

.cell-group-mark {
  width: 16px;
  height: 16px;
  border: 1px solid #d7dee8;
  border-radius: 4px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #667085;
  font-size: 11px;
  line-height: 1;
  background: #fff;
}

.cell-group-title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cell-group-count {
  color: #98a2b3;
  font-size: 12px;
  font-weight: 500;
}

.cell-group-toggle:hover .cell-group-mark {
  border-color: #409eff;
  color: #1d4ed8;
}

.pivot-cell-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding-left: 21px;
}

.instrument-chip {
  max-width: 128px;
  padding: 4px 8px;
  border: 1px solid #d7dee8;
  border-radius: 999px;
  background: #fff;
  color: #1f2937;
  font-size: 12px;
  line-height: 1.3;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}

.instrument-chip:hover,
.instrument-chip.active {
  border-color: #409eff;
  background: #eef6ff;
  color: #1d4ed8;
}

.instrument-chip.active {
  font-weight: 650;
  box-shadow: 0 0 0 1px #409eff inset;
}

.pivot-empty {
  padding: 28px 14px;
  color: #6b7280;
}

.instrument-detail-pane {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.instrument-detail-card {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.instrument-kv-list {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  padding: 8px 14px 14px;
  color: #1f2937;
  font-size: 13px;
}

.instrument-kv-item {
  display: grid;
  grid-template-columns: 110px minmax(0, 1fr);
  gap: 14px;
  padding: 9px 0;
  border-bottom: 1px solid #eef0f3;
}

.instrument-kv-item:last-child {
  border-bottom: 0;
}

.detail-label {
  color: #6b7280;
  font-size: 12px;
  line-height: 1.7;
}

.instrument-detail-empty {
  flex: 1 1 auto;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #6b7280;
  background: #fbfcfe;
}

.workspace-layout {
  display: grid;
  grid-template-columns: minmax(240px, 300px) minmax(0, 960px);
  width: min(100%, 1260px);
  flex: 1 1 auto;
  min-height: 0;
  gap: 18px;
  align-items: start;
  overflow: auto;
  padding-right: 4px;
}

.history-pane {
  position: sticky;
  top: 0;
  height: fit-content;
  max-height: 100%;
  border: 1px solid var(--music-line);
  border-radius: 8px;
  overflow: hidden;
  background: var(--music-panel);
}

.pane-title {
  padding: 12px 13px;
  font-weight: 700;
  color: var(--music-text);
  border-bottom: 1px solid var(--music-line);
}

.history-empty {
  padding: 14px 12px;
  color: #6b7280;
}

.history-item {
  width: 100%;
  padding: 10px 13px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  border: 0;
  border-bottom: 1px solid #edf1f6;
  background: #fff;
  color: inherit;
  text-align: left;
  cursor: pointer;
}

.history-item:last-child {
  border-bottom: 0;
}

.history-item.active {
  background: #edf4ff;
  box-shadow: inset 3px 0 0 var(--music-blue);
}

.history-name {
  color: var(--music-text);
  font-weight: 600;
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-meta {
  color: var(--music-muted);
  font-size: 12px;
}

.workspace {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
  padding-bottom: 20px;
}

.active-title {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}

.active-name {
  min-width: 0;
  font-size: 18px;
  font-weight: 700;
  color: var(--music-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.active-meta {
  flex: 0 0 auto;
  color: var(--music-muted);
  font-size: 12px;
}

.transport {
  min-height: 34px;
  gap: 12px;
}

.timeline {
  flex: 1;
  min-width: 180px;
}

.time-text {
  width: 48px;
  font-variant-numeric: tabular-nums;
  color: #475467;
  text-align: center;
}

.stem-table {
  border: 1px solid var(--music-line);
  border-radius: 8px;
  overflow: hidden;
  background: var(--music-panel);
}

.stem-row {
  --stem-accent: var(--music-accent);
  --stem-title-color: var(--music-text);

  min-height: 66px;
  padding: 9px 14px 10px;
  border-bottom: 1px solid #edf1f6;
}

.stem-row:last-child {
  border-bottom: 0;
}

.original-stem {
  --stem-accent: #64748b;
  --stem-title-color: #334155;

  background: #f8fafc;
}

.original-stem .stem-title {
  color: var(--stem-title-color);
}

.stem-switch {
  --el-switch-on-color: var(--stem-accent);
}

.stem-control-row {
  gap: 12px;
  min-height: 24px;
}

.stem-heading {
  min-width: 0;
  flex: 1;
  gap: 10px;
}

.stem-title {
  flex: 0 0 74px;
  font-weight: 700;
  color: var(--stem-title-color);
}

.stem-file {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: var(--music-muted);
}

.stem-wave-row {
  margin-top: 6px;
  margin-left: 54px;
}

.waveform {
  position: relative;
  width: 100%;
  height: 30px;
  padding: 0;
  border: 0;
  border-radius: 4px;
  display: flex;
  align-items: center;
  gap: 1px;
  background: #f1f4f8;
  overflow: hidden;
  cursor: pointer;
}

.waveform:disabled {
  cursor: default;
  opacity: 0.55;
}

.waveform-bar {
  flex: 1 1 0;
  min-width: 1px;
  border-radius: 1px;
  background: var(--stem-accent);
  opacity: 0.68;
}

.waveform-playhead {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 2px;
  background: #111827;
  opacity: 0.72;
  transform: translateX(-1px);
  pointer-events: none;
}

.volume-slider {
  --el-slider-main-bg-color: var(--stem-accent);

  width: 170px;
}

.creative-brief-panel {
  border: 1px solid var(--music-line);
  border-radius: 8px;
  background: var(--music-panel);
}

.creative-brief-head {
  min-height: 48px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--music-line);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.creative-title {
  font-weight: 700;
  color: var(--music-text);
}

.creative-meta,
.prompt-label {
  font-size: 12px;
  color: var(--music-muted);
}

.prompt-label {
  min-height: 22px;
  margin-bottom: 4px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.prompt-label button {
  height: 22px;
  padding: 0 8px;
  border: 1px solid #cfe0f5;
  border-radius: 5px;
  color: var(--music-blue);
  font-size: 12px;
  background: #f8fbff;
  cursor: pointer;
}

.prompt-actions {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.creative-brief-body {
  padding: 12px;
  display: grid;
  gap: 10px;
  color: var(--music-text);
  font-size: 13px;
  line-height: 1.65;
}

.manual-copy-panel {
  margin: 10px 12px 0;
  border: 1px solid #f6d28b;
  border-radius: 6px;
  overflow: hidden;
  background: #fffbeb;
}

.manual-copy-head {
  min-height: 32px;
  padding: 6px 9px;
  border-bottom: 1px solid #f8e3ad;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.manual-copy-head span {
  color: #92400e;
  font-size: 12px;
  font-weight: 650;
}

.manual-copy-head button {
  height: 24px;
  padding: 0 8px;
  border: 1px solid #f6d28b;
  border-radius: 5px;
  color: #92400e;
  background: #fff7d6;
  cursor: pointer;
}

.manual-copy-panel textarea {
  width: 100%;
  min-height: 150px;
  padding: 9px;
  border: 0;
  box-sizing: border-box;
  resize: vertical;
  color: var(--music-text);
  font-size: 12px;
  line-height: 1.6;
  background: #fffdf5;
}

.creative-brief-body p {
  margin: 0;
}

.creative-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.creative-tags span {
  padding: 3px 8px;
  border-radius: 999px;
  color: #0f766e;
  background: #e5f7f4;
}

.feature-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(96px, 1fr));
  gap: 8px;
}

.feature-grid > div {
  padding: 8px 9px;
  border: 1px solid #edf2f8;
  border-radius: 6px;
  display: grid;
  gap: 3px;
  background: #fbfdff;
}

.feature-grid span {
  font-size: 12px;
  color: var(--music-muted);
}

.feature-grid strong {
  font-size: 13px;
  color: var(--music-text);
}

.style-profile-panel {
  padding: 10px;
  border: 1px solid #dbeafe;
  border-radius: 6px;
  display: grid;
  gap: 8px;
  background: #f8fbff;
}

.style-profile-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.style-profile-head > div {
  min-width: 0;
  display: grid;
  gap: 2px;
}

.style-profile-head span {
  color: var(--music-muted);
  font-size: 12px;
}

.style-profile-head strong {
  color: var(--music-text);
  font-size: 15px;
}

.style-profile-head button {
  height: 24px;
  padding: 0 9px;
  border: 1px solid #cfe0f5;
  border-radius: 5px;
  color: var(--music-blue);
  font-size: 12px;
  background: #fff;
  cursor: pointer;
}

.style-profile-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.style-profile-tags span {
  padding: 3px 8px;
  border-radius: 999px;
  color: #2e82f0;
  font-size: 12px;
  background: #edf4ff;
}

.style-profile-reasons {
  display: grid;
  gap: 4px;
}

.style-profile-reasons p {
  color: #344054;
  font-size: 12px;
  line-height: 1.55;
}

.style-score-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(118px, max-content));
  gap: 6px;
}

.style-score-list > div {
  min-width: 118px;
  padding: 5px 8px;
  border: 1px solid #edf2f8;
  border-radius: 5px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  background: #fff;
}

.style-score-list span {
  color: var(--music-muted);
  font-size: 12px;
}

.style-score-list strong {
  color: var(--music-text);
  font-size: 12px;
}

.section-strip {
  min-height: 38px;
  border: 1px solid #edf2f8;
  border-radius: 6px;
  overflow: hidden;
  display: flex;
  background: #f8fafc;
}

.section-segment {
  min-width: 70px;
  padding: 7px 9px;
  border-right: 1px solid rgba(255, 255, 255, 0.74);
  display: grid;
  align-content: center;
  gap: 2px;
}

.section-segment:last-child {
  border-right: 0;
}

.section-segment span {
  font-size: 12px;
  color: #475467;
}

.section-segment strong {
  color: var(--music-text);
  font-size: 13px;
}

.section-segment.energy-高 {
  background: #dff6f2;
}

.section-segment.energy-中 {
  background: #edf4ff;
}

.section-segment.energy-低 {
  background: #f7f9fc;
}

.style-direction-row {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}

.style-direction-row button {
  height: 28px;
  padding: 0 10px;
  border: 1px solid #cfe0f5;
  border-radius: 999px;
  color: #2e82f0;
  font-size: 12px;
  font-weight: 650;
  background: #f8fbff;
  cursor: pointer;
}

.style-direction-row button.active {
  border-color: #13a394;
  color: #0f766e;
  background: #e5f7f4;
}

.style-direction-meta {
  padding: 8px 10px;
  border: 1px solid #edf2f8;
  border-radius: 6px;
  display: grid;
  gap: 3px;
  background: #fbfdff;
}

.style-direction-meta span {
  color: var(--music-muted);
  font-size: 12px;
}

.style-direction-meta strong {
  color: var(--music-text);
  font-size: 13px;
  font-weight: 650;
}

.suno-field-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}

.suno-field-card {
  min-width: 0;
  padding: 9px;
  border: 1px solid #edf2f8;
  border-radius: 6px;
  background: #fbfdff;
}

.suno-field-card p {
  color: var(--music-text);
  font-size: 12px;
  line-height: 1.55;
}

.creative-recipe-list {
  border: 1px solid #edf2f8;
  border-radius: 6px;
  overflow: hidden;
  background: #fff;
}

.style-preset-panel {
  border: 1px solid #edf2f8;
  border-radius: 6px;
  overflow: hidden;
  background: #fff;
}

.style-preset-tabs {
  padding: 9px 10px;
  border-bottom: 1px solid #edf2f8;
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}

.style-preset-tabs button {
  height: 28px;
  padding: 0 10px;
  border: 1px solid #d5dfeb;
  border-radius: 6px;
  color: #475467;
  font-size: 12px;
  font-weight: 650;
  background: #fff;
  cursor: pointer;
}

.style-preset-tabs button.active {
  border-color: rgba(19, 163, 148, 0.28);
  color: #0f766e;
  background: #effbf9;
}

.style-preset-detail {
  padding: 10px;
  display: grid;
  gap: 8px;
}

.style-preset-main {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.style-preset-main > div {
  min-width: 0;
  display: grid;
  gap: 2px;
}

.style-preset-main strong {
  color: var(--music-text);
  font-size: 14px;
}

.style-preset-main span,
.style-preset-grid span {
  color: var(--music-muted);
  font-size: 12px;
}

.style-preset-main button {
  height: 24px;
  padding: 0 9px;
  border: 1px solid #cfe0f5;
  border-radius: 5px;
  color: var(--music-blue);
  background: #f8fbff;
  cursor: pointer;
  flex: 0 0 auto;
}

.platform-open-actions {
  flex: 0 0 auto;
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px;
}

.platform-open-actions button {
  height: 24px;
  padding: 0 9px;
  border: 1px solid #cfe0f5;
  border-radius: 5px;
  color: var(--music-blue);
  background: #f8fbff;
  cursor: pointer;
}

.style-preset-palette {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.style-preset-palette span {
  padding: 2px 7px;
  border-radius: 999px;
  color: #0f766e;
  font-size: 12px;
  background: #e5f7f4;
}

.style-preset-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.style-preset-grid > div {
  padding: 8px;
  border: 1px solid #edf2f8;
  border-radius: 6px;
  display: grid;
  gap: 2px;
  background: #fbfdff;
}

.style-preset-grid p {
  margin: 0;
  color: #475467;
  font-size: 12px;
  line-height: 1.6;
}

.creative-recipe-item {
  padding: 10px;
  border-bottom: 1px solid #edf2f8;
  display: grid;
  gap: 8px;
}

.creative-recipe-item:last-child {
  border-bottom: 0;
}

.creative-recipe-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.creative-recipe-head > div {
  min-width: 0;
  display: grid;
  gap: 2px;
}

.creative-recipe-head strong {
  color: var(--music-text);
  font-size: 14px;
}

.creative-recipe-head span,
.creative-recipe-columns span {
  color: var(--music-muted);
  font-size: 12px;
}

.creative-recipe-head button,
.creative-recipe-actions button {
  height: 24px;
  padding: 0 9px;
  border: 1px solid #cfe0f5;
  border-radius: 5px;
  color: var(--music-blue);
  background: #f8fbff;
  cursor: pointer;
  flex: 0 0 auto;
}

.creative-recipe-tags,
.creative-recipe-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.creative-recipe-tags span {
  padding: 2px 7px;
  border-radius: 999px;
  color: #0f766e;
  font-size: 12px;
  background: #e5f7f4;
}

.creative-recipe-columns {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.creative-recipe-columns > div {
  padding: 8px;
  border: 1px solid #edf2f8;
  border-radius: 6px;
  display: grid;
  gap: 2px;
  background: #fbfdff;
}

.creative-recipe-moves {
  margin: 0;
  padding-left: 20px;
  color: #475467;
  font-size: 12px;
  line-height: 1.6;
}

.prompt-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.prompt-grid > div {
  padding: 10px;
  border: 1px solid #edf2f8;
  border-radius: 6px;
  background: #fbfdff;
}

.variant-list {
  display: grid;
  gap: 8px;
}

.variant-item {
  padding: 10px;
  border: 1px solid #edf2f8;
  border-radius: 6px;
  background: #fff;
}

.arrangement-plan,
.stem-insight-list {
  border: 1px solid #edf2f8;
  border-radius: 6px;
  overflow: hidden;
  background: #fff;
}

.arrangement-item,
.stem-insight-item {
  padding: 9px 10px;
  border-bottom: 1px solid #edf2f8;
  display: grid;
  gap: 3px;
}

.arrangement-item:last-child,
.stem-insight-item:last-child {
  border-bottom: 0;
}

.arrangement-item span,
.stem-insight-item span {
  color: var(--music-muted);
  font-size: 12px;
}

.arrangement-item strong,
.stem-insight-item strong {
  color: var(--music-text);
  font-size: 13px;
}

.stem-insight-item > div {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}

.prompt-record-list {
  border: 1px solid #edf2f8;
  border-radius: 6px;
  overflow: hidden;
}

.prompt-record-title {
  padding: 8px 10px;
  border-bottom: 1px solid #edf2f8;
  color: var(--music-text);
  font-weight: 700;
  background: #fbfdff;
}

.prompt-record-item {
  min-height: 42px;
  padding: 8px 10px;
  border-bottom: 1px solid #edf2f8;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.prompt-record-item:last-child {
  border-bottom: 0;
}

.prompt-record-item > div {
  min-width: 0;
  display: grid;
  gap: 2px;
}

.prompt-record-item strong {
  color: var(--music-text);
}

.prompt-record-item span {
  color: var(--music-muted);
  font-size: 12px;
}

.prompt-record-item button {
  height: 24px;
  padding: 0 9px;
  border: 1px solid #cfe0f5;
  border-radius: 5px;
  color: var(--music-blue);
  background: #f8fbff;
  cursor: pointer;
}

.score-panel {
  border: 1px solid var(--music-line);
  border-radius: 8px;
  overflow: hidden;
  background: var(--music-panel);
}

.score-toolbar {
  min-height: 46px;
  padding: 8px 12px;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  border-bottom: 1px solid var(--music-line);
}

.score-left {
  min-width: 0;
  flex: 1 1 280px;
  gap: 10px;
}

.score-heading {
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.score-title {
  min-width: 0;
  font-weight: 700;
  color: var(--music-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.score-meta {
  font-size: 12px;
  color: var(--music-muted);
}

.score-tools {
  flex: 0 1 auto;
  max-width: 100%;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  flex-wrap: wrap;
}

.score-mode-tabs {
  flex: 0 1 auto;
  max-width: 100%;
}

.score-actions {
  gap: 6px;
  flex: 0 0 auto;
  display: flex;
  align-items: center;
}

.score-link {
  min-width: 44px;
  height: 28px;
  padding: 0 9px;
  border: 1px solid #d8e1ec;
  border-radius: 4px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: var(--music-blue);
  text-decoration: none;
  background: #fff;
}

.score-toolbar :deep(.el-radio-button__inner) {
  height: 28px;
  padding: 0 10px;
  border-color: #d8e1ec;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  line-height: 1;
  background: #fff;
  color: #475467;
}

.score-toolbar :deep(.el-radio-button__original-radio:checked + .el-radio-button__inner) {
  border-color: var(--music-blue);
  background: var(--music-blue);
  color: #fff;
  box-shadow: -1px 0 0 0 var(--music-blue);
}

.score-link:hover {
  border-color: #9cc8ff;
  background: #eef6ff;
}

.score-transport {
  gap: 12px;
  padding: 8px 12px;
  border-bottom: 1px solid #edf1f6;
}

.jianpu-line {
  padding: 10px 12px;
  border-bottom: 1px solid #edf1f6;
  color: var(--music-text);
  font-family: ui-monospace, SFMono-Regular, Consolas, 'Liberation Mono', monospace;
  font-size: 13px;
  line-height: 1.75;
  background: #fbfcfe;
  word-break: break-word;
}

.score-loading {
  padding: 12px;
  color: #6b7280;
}

.score-empty {
  display: flex;
  align-items: center;
  min-height: 88px;
  padding: 12px;
  color: var(--music-muted);
  background: #f8fafc;
  border-top: 1px solid var(--music-line);
}

.piano-roll {
  height: 258px;
  background: #1e2a35;
}

.roll-lane {
  position: relative;
  height: 206px;
  margin: 0 12px;
  overflow: hidden;
  background:
    linear-gradient(to right, rgba(255, 255, 255, 0.055) 1px, transparent 1px) 0 0 / calc(100% / var(--visible-keys)) 100%,
    linear-gradient(to bottom, rgba(255, 255, 255, 0.07), transparent 46%);
}

.roll-note {
  position: absolute;
  border-radius: 3px;
  background: #38bdf8;
  box-shadow: 0 0 0 1px rgba(15, 23, 42, 0.35), 0 0 10px rgba(56, 189, 248, 0.3);
  opacity: 0.9;
}

.roll-note.right_chord {
  background: #22c55e;
  box-shadow: 0 0 0 1px rgba(15, 23, 42, 0.35), 0 0 10px rgba(34, 197, 94, 0.3);
}

.roll-note.left {
  background: #f59e0b;
  box-shadow: 0 0 0 1px rgba(15, 23, 42, 0.35), 0 0 10px rgba(245, 158, 11, 0.32);
}

.roll-note.active {
  filter: brightness(1.25);
}

.piano-keyboard {
  position: relative;
  height: 52px;
  margin: 0 12px;
  display: grid;
  grid-template-columns: repeat(var(--visible-keys), minmax(2px, 1fr));
  border-top: 2px solid #111827;
  background: #111827;
}

.piano-key {
  height: 52px;
  border-right: 1px solid #9ca3af;
  background: #f9fafb;
}

.piano-key.black {
  height: 32px;
  margin-inline: -35%;
  z-index: 1;
  border-right: 0;
  border-radius: 0 0 2px 2px;
  background: #111827;
}

.piano-key.active {
  background: #93c5fd;
}

.piano-key.black.active {
  background: #3b82f6;
}

.empty-state {
  min-height: 220px;
  padding: 36px;
  border: 1px solid var(--music-line);
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  color: var(--music-muted);
  background: var(--music-panel);
}

.empty-title {
  font-size: 18px;
  font-weight: 700;
  color: var(--music-text);
  margin-bottom: 6px;
}

audio {
  display: none;
}

@media (max-width: 820px) {
  .music-tools-page {
    padding: 16px;
    overflow: auto;
  }

  .workspace-layout {
    grid-template-columns: 1fr;
    overflow: visible;
  }

  .page-head {
    align-items: flex-start;
    flex-direction: column;
  }

  .title-block,
  .command-bar,
  .command-fields {
    align-items: flex-start;
    flex-direction: column;
  }

  .command-mode {
    width: 100%;
  }

  .command-mode button {
    flex: 1 1 0;
  }

  .multitrack-source-head {
    align-items: flex-start;
    flex-direction: column;
  }

  .multitrack-source-list {
    grid-template-columns: 1fr;
  }

  .multitrack-study-head,
  .multitrack-study-actions {
    align-items: flex-start;
    flex-direction: column;
  }

  .multitrack-study-grid {
    grid-template-columns: 1fr;
  }

  .multitrack-study-grid div {
    border-right: 0;
  }

  .suno-field-grid,
  .style-preset-grid,
  .creative-recipe-columns,
  .prompt-grid {
    grid-template-columns: 1fr;
  }

  .history-pane {
    position: static;
  }

  .stem-row {
    padding: 10px 12px;
  }

  .stem-control-row {
    align-items: flex-start;
    flex-wrap: wrap;
  }

  .stem-heading {
    flex-basis: calc(100% - 60px);
    flex-direction: column;
    align-items: flex-start;
    gap: 2px;
  }

  .stem-wave-row {
    margin-left: 0;
  }

  .file-picker,
  .command-file,
  .engine-select,
  .multitrack-source-select,
  .multitrack-url-input,
  .volume-slider,
  .recording-preview {
    width: 100%;
    max-width: none;
  }
}

</style>
