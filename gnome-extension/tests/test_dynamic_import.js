const url = '../aiuClient.js?v=' + Date.now();
const mod = await import(url);
console.log('Dynamic import works!', Object.keys(mod));
