<template>
  <div class="author-contact-page">
    <main class="author-contact-content">
      <h1>联系作者：代号4101</h1>

      <div class="contact-copy">
        <p>
          如果你想学习如何用 AI 开发 CodeYun 这样的工具网站，
          或者想让我提供现成的开发服务，帮你实现专属的网站工具，可以联系我。
        </p>
        <p>
          我的微信：<strong>18850340559</strong>。添加好友请注明来意。
        </p>
        <p>
          初步沟通免咨询费、顾问费。服务仅限远程教学和开发支持，
          正式服务收费 <strong>100 元/小时</strong>。
        </p>
        <p>
          我会使用 AI 辅助开发，尽量提高实现速度和效率；
          计费只计算我实际投入的有效工时。
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
