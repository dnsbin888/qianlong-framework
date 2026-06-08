// sidebar_fix.js — 强制 Streamlit 侧边栏宽度 = 300px
// 覆盖 Streamlit JS 动态写入的内联样式
(function() {
    function fix() {
        // 外层 section
        var sb = document.querySelector('section[data-testid="stSidebar"]');
        if (sb) {
            sb.style.width = '300px';
            sb.style.minWidth = '300px';
            sb.style.maxWidth = '300px';
            sb.style.flex = '0 0 300px';
        }
        // 内层内容区
        var content = document.querySelector('[data-testid="stSidebarContent"]');
        if (content) {
            content.style.width = '300px';
            content.style.minWidth = '300px';
            content.style.maxWidth = '300px';
        }
        // 主内容区左边距
        var main = document.querySelector('section[data-testid="stMain"] > div');
        if (main) {
            main.style.marginLeft = '300px';
        }
    }

    // 立即执行一次
    fix();

    // 监听 DOM 变化（Streamlit 可能随时重写 style）
    if (window.MutationObserver) {
        new MutationObserver(fix).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['style'] });
    }

    // 定时兜底
    setInterval(fix, 500);
})();
