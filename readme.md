Drain and kill GCP's Pre-Emptible node in a GKE cluster before it's natural death time.

We do this for a few reasons:

* Google only sends 30 seconds notice, which is not enough to drain all the pods in a node.
* When starting a new pool of pre-emptible node, there's a higher chance that they get terminated in the same time (after 24h)

Dockerhub: https://hub.docker.com/r/splitmedialabs/noderecycler/tags/
