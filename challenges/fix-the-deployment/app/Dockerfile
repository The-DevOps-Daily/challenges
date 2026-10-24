FROM golang:1.27-alpine@sha256:b8c9b82affbbb82a020bbcf998dad2ba9516944ac7e883524642a481a9505e9c AS build
WORKDIR /src
COPY go.mod main.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /inventory-api .

FROM scratch
COPY --from=build /inventory-api /inventory-api
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/inventory-api"]
