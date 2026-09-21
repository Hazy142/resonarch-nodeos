#include <cuda_runtime.h>
#include <cstdint>
#include <cstdio>
#include <cstdlib>

__global__ void nodeos_probe(std::uint32_t *value) {
    if (blockIdx.x == 0 && threadIdx.x == 0) *value = 0x4e4f4445u;
}
static void fail(const char *stage, cudaError_t error) {
    std::fprintf(stderr, "%s: %s\n", stage, cudaGetErrorString(error));
    std::printf("{\"schema\":\"resonarch.nodeos.cuda-smoke.v1\",\"status\":\"FAIL\",\"stage\":\"%s\"}\n", stage);
    std::exit(1);
}
int main() {
    int count = 0;
    cudaError_t rc = cudaGetDeviceCount(&count);
    if (rc != cudaSuccess) fail("cudaGetDeviceCount", rc);
    if (count < 1) {
        std::puts("{\"schema\":\"resonarch.nodeos.cuda-smoke.v1\",\"status\":\"FAIL\",\"stage\":\"no-device\"}");
        return 1;
    }
    cudaDeviceProp prop{};
    rc = cudaGetDeviceProperties(&prop, 0);
    if (rc != cudaSuccess) fail("cudaGetDeviceProperties", rc);
    std::uint32_t *device = nullptr;
    rc = cudaMalloc(&device, sizeof(std::uint32_t));
    if (rc != cudaSuccess) fail("cudaMalloc", rc);
    nodeos_probe<<<1, 1>>>(device);
    rc = cudaDeviceSynchronize();
    if (rc != cudaSuccess) fail("kernel", rc);
    std::uint32_t host = 0;
    rc = cudaMemcpy(&host, device, sizeof(host), cudaMemcpyDeviceToHost);
    if (rc != cudaSuccess) fail("cudaMemcpy", rc);
    cudaFree(device);
    const bool pass = host == 0x4e4f4445u;
    std::printf(
        "{\"schema\":\"resonarch.nodeos.cuda-smoke.v1\",\"status\":\"%s\","
        "\"device\":\"%s\",\"compute_capability\":\"%d.%d\",\"word\":\"0x%08x\"}\n",
        pass ? "PASS" : "FAIL", prop.name, prop.major, prop.minor, host);
    return pass ? 0 : 1;
}
