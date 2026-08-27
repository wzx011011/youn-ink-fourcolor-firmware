#ifndef AUDIO_PACKET_H
#define AUDIO_PACKET_H

#include <cstdint>
#include <vector>

// One opus/pcm frame flowing through the audio pipeline queues.
// Formerly lived in protocols/protocol.h (xiaozhi port leftover whose
// Protocol class has no implementation in this project); only this struct
// was ever used, so it moved here when protocol.h was removed.
struct AudioStreamPacket {
    int sample_rate = 0;
    int frame_duration = 0;
    uint32_t timestamp = 0;
    std::vector<uint8_t> payload;
};

#endif  // AUDIO_PACKET_H
