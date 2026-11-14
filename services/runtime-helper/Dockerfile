# Build the verified static binary with ./build.sh before building this image.
# There is no runtime base-image or builder-image dependency.
FROM scratch
COPY --chmod=0555 .build/helper /helper
USER 1000:1000
ENTRYPOINT ["/helper"]
CMD ["install", "/runtime/helper"]
