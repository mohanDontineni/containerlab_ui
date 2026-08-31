FROM alpine:3.22.5

RUN apk add --no-cache iproute2 nftables tcpdump busybox-extras \
    && mkdir -p /etc/studio

COPY entrypoint.sh /usr/local/bin/studio-firewall-entrypoint
RUN chmod 0755 /usr/local/bin/studio-firewall-entrypoint

ENTRYPOINT ["/usr/local/bin/studio-firewall-entrypoint"]
CMD ["sleep", "infinity"]
