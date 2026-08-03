const http = require('http');

const postData = JSON.stringify({
  phoneNumber: '18574261739'
});

const options = {
  hostname: 'localhost',
  port: 9235,
  path: '/auth/pairing-code',
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'Content-Length': Buffer.byteLength(postData)
  }
};

const req = http.request(options, (res) => {
  let data = '';
  res.on('data', (chunk) => { data += chunk; });
  res.on('end', () => { console.log('Response:', data); });
});

req.on('error', (e) => { console.error('Error:', e.message); });
req.write(postData);
req.end();
