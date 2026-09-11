<template>
  <div class="author-contact-page">
    <main class="author-contact-content">
      <h1>联系作者：代号4101</h1>

      <div class="contact-copy">
        <p>
          如果你想学习如何用 AI 开发 CodeYun 这样的工具网站，
          欢迎微信交流：<strong>18850340559</strong>。
        </p>
      </div>
    </main>
  </div>
</template>

<style scoped>
.author-contact-page {
  min-height: 100%;
  background: #f6f8fb;
  padding: 40px;
  box-sizing: border-box;
}

.author-contact-content {
  width: min(820px, 100%);
  color: #1f2937;
}

h1 {
  margin: 0 0 24px;
  color: #111827;
  font-size: 32px;
  line-height: 1.25;
}

.contact-copy {
  display: grid;
  gap: 14px;
  color: #334155;
  font-size: 16px;
  line-height: 1.85;
}

.contact-copy p {
  margin: 0;
}

.contact-copy strong {
  color: #111827;
  font-weight: 700;
}

@media (max-width: 640px) {
  .author-contact-page {
    padding: 24px;
  }

  h1 {
    font-size: 28px;
  }

  .contact-copy {
    font-size: 15px;
  }
}
</style>
