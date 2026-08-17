(function () {
  // Mermaid init
  if (window.mermaid) {
    mermaid.initialize({
      startOnLoad: true,
      theme: 'neutral',
      securityLevel: 'loose',
      flowchart: { htmlLabels: true, curve: 'basis' },
      themeVariables: {
        primaryColor: '#f2f0ff',
        primaryBorderColor: '#4b3fe3',
        primaryTextColor: '#1a1b25',
        lineColor: '#5b5e6e',
        fontFamily: 'Outfit, PingFang SC, Noto Sans CJK SC, sans-serif'
      }
    });
  }

  // TOC scroll-spy
  var links = Array.prototype.slice.call(document.querySelectorAll('nav.toc a'));
  var map = {};
  links.forEach(function (a) {
    var id = a.getAttribute('href').slice(1);
    var sec = document.getElementById(id);
    if (sec) map[id] = a;
  });
  var sections = Object.keys(map).map(function (id) { return document.getElementById(id); });

  function onScroll() {
    var current = null;
    sections.forEach(function (sec) {
      var rect = sec.getBoundingClientRect();
      if (rect.top <= 120) current = sec.id;
    });
    links.forEach(function (a) { a.classList.remove('active'); });
    if (current && map[current]) map[current].classList.add('active');
  }
  document.addEventListener('scroll', onScroll, { passive: true });
  onScroll();
})();
