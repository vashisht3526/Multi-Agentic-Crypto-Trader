#!/bin/bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
nvm install 22
cd /home/azureuser/cryptotrader/frontend
npm install
npm install pm2 -g
pm2 start "npm run dev -- --host 0.0.0.0 --port 5173" --name cryptotrader-frontend
pm2 save
pm2 startup
