FROM eclipse-temurin:21-jre-jammy

ARG JENA_VERSION=6.2.0

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL \
    "https://dlcdn.apache.org/jena/binaries/apache-jena-fuseki-${JENA_VERSION}.tar.gz" \
    -o /tmp/fuseki.tar.gz \
    && tar -xzf /tmp/fuseki.tar.gz -C /opt \
    && mv "/opt/apache-jena-fuseki-${JENA_VERSION}" /fuseki \
    && rm /tmp/fuseki.tar.gz

WORKDIR /fuseki

EXPOSE 3030

ENTRYPOINT ["/fuseki/fuseki-server"]