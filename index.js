const express = require('express');
const app = express();
const PORT = 3000;

app.get('/', (req, res) => {
  res.send('热更新测试');
});

app.listen(PORT,'0.0.0.0', () => {
  console.log(`服务器运行在 http://localhost:${PORT}`);
});
