#!/bin/bash

# Install rsyslog and TLS support
apt-get install -y rsyslog rsyslog-openssl > /dev/null 2>&1

# Configure rsyslog for SolarWinds if rsyslog is available
if command -v rsyslogd > /dev/null 2>&1; then
    mkdir -p /etc/rsyslog.d

    cat > /etc/rsyslog.d/99-solarwinds.conf << EOF
\$DefaultNetstreamDriverCAFile /etc/ssl/certs/ca-certificates.crt
\$ActionSendStreamDriver ossl
\$ActionSendStreamDriverMode 1
\$ActionSendStreamDriverAuthMode x509/name
\$ActionSendStreamDriverPermittedPeer *.collector.ap-01.cloud.solarwinds.com

\$template SWOFormat,"<%pri%>1 %timestamp:::date-rfc3339% %HOSTNAME% %app-name% %procid% %msgid% [${SYSLOG_TOKEN}@41058]%msg:::sp-if-no-1st-sp%%msg%"

*.* @@syslog.collector.ap-01.cloud.solarwinds.com:6514;SWOFormat
EOF

    rsyslogd || service rsyslog start || true
    echo "rsyslog configured and started."
else
    echo "rsyslog not available, skipping syslog setup."
fi

exec gunicorn main:app
