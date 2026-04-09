#!/bin/bash

# Install rsyslog TLS support if not present
apt-get install -y rsyslog-openssl > /dev/null 2>&1

# Write SolarWinds rsyslog config
cat > /etc/rsyslog.d/99-solarwinds.conf << EOF
\$DefaultNetstreamDriverCAFile /etc/ssl/certs/ca-certificates.crt
\$ActionSendStreamDriver ossl
\$ActionSendStreamDriverMode 1
\$ActionSendStreamDriverAuthMode x509/name
\$ActionSendStreamDriverPermittedPeer *.collector.ap-01.cloud.solarwinds.com

\$template SWOFormat,"<%pri%>1 %timestamp:::date-rfc3339% %HOSTNAME% %app-name% %procid% %msgid% [${SYSLOG_TOKEN}@41058]%msg:::sp-if-no-1st-sp%%msg%"

*.* @@syslog.collector.ap-01.cloud.solarwinds.com:6514;SWOFormat
EOF

service rsyslog restart

exec gunicorn main:app
