// Headers
#include <stdio.h>    // printf, perror
#include <stdlib.h>   // strtoul, exit
#include <stdint.h>   // uint32_t
#include <fcntl.h>    // open, O_RDWR
#include <unistd.h>   // close
#include <sys/mman.h> // mmap, munmap, PROT_*, MAP_*

int main(int argc, char *argv[])
{
    // Open device
    int fd = open("/dev/uio0", O_RDWR);
    if (fd < 0)
    {
        perror("open");
        exit(1);
    }

    // Map the registers
    void *map = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (map == MAP_FAILED)
    {
        perror("mmap");
        exit(1);
    }

    // Pointer to map (volatile pls)
    // Note: volatile so ever R/W actually emits
    volatile uint32_t *regs = (volatile uint32_t *)map;

    if (argc < 2)
    {
        // count<2, wtf
        fprintf(stderr, "usage: %s <index> [value]\n", argv[0]);
        exit(1);
    }

    // parse argv
    unsigned long index = strtoul(argv[1], NULL, 0);

    if (argc == 2)
    {
        // READ (count=2)
        uint32_t val = regs[index];
        printf("0x%08x\n", val);
    }
    else if (argc == 3)
    {
        // WRITE (count=3)
        unsigned long val = strtoul(argv[2], NULL, 0);
        regs[index] = (uint32_t)val;
    }
    else
    {
        // count=?, wtf
        fprintf(stderr, "usage: %s <index> [value]\n", argv[0]);
        exit(1);
    }

    // Cleanup
    munmap(map, 4096);
    close(fd);
    return 0;
}