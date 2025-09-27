<script setup lang="ts">
import LocaleText from "@/components/text/localeText.vue";
import {computed, onMounted, ref, watch} from "vue";

import {
	Chart,
	LineElement,
	BarElement,
	BarController,
	LineController,
	LinearScale,
	Legend,
	Title,
	Tooltip,
	CategoryScale,
	PointElement,
	Filler
} from 'chart.js';
Chart.register(
	LineElement,
	BarElement,
	BarController,
	LineController,
	LinearScale,
	Legend,
	Title,
	Tooltip,
	CategoryScale,
	PointElement,
	Filler
);
import PeerSessions from "@/components/peerDetailsModalComponents/peerSessions.vue";
import PeerTraffics from "@/components/peerDetailsModalComponents/peerTraffics.vue";
import PeerEndpoints from "@/components/peerDetailsModalComponents/peerEndpoints.vue";
import {useRoute} from "vue-router";
import {fetchGet} from "@/utilities/fetch.js";

const props = defineProps(['selectedPeer'])
const selectedDate = ref(undefined)
const route = useRoute()
const limitInfo = ref({
        activeSessions: 0,
        maxConcurrent: null,
        policy: "new_wins"
})
const usageSessions = ref([])
defineEmits(['close'])

const maxConcurrentDisplay = computed(() => {
        if (!limitInfo.value) return null
        const value = limitInfo.value.maxConcurrent
        return value && value > 0 ? value : null
})

const policyLabel = computed(() => limitInfo.value.policy === 'old_wins' ? 'Keep existing connection' : 'New connection replaces old')

const formatAge = (seconds?: number | null) => {
        if (seconds === undefined || seconds === null) return '—'
        if (seconds < 60) return `${seconds}s`
        if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
        if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`
        return `${Math.floor(seconds / 86400)}d`
}

const formatBytes = (bytes?: number) => {
        if (!bytes) return '0 B'
        const units = ['B', 'KB', 'MB', 'GB', 'TB']
        let size = bytes
        let unitIndex = 0
        while (size >= 1024 && unitIndex < units.length - 1){
                size /= 1024
                unitIndex += 1
        }
        return `${size.toFixed(1)} ${units[unitIndex]}`
}

const loadSessionInfo = () => {
        if (!props.selectedPeer){
                return
        }
        const peerId = props.selectedPeer.id
        fetchGet(`/api/peers/${route.params.id}/${peerId}/limits`, {}, (res) => {
                if (res.status && res.data){
                        limitInfo.value = res.data
                }
        })
        fetchGet(`/api/peers/${route.params.id}/${peerId}/usage`, {}, (res) => {
                if (res.status && res.data){
                        usageSessions.value = res.data.sessions || []
                }
        })
}

onMounted(() => loadSessionInfo())
watch(() => props.selectedPeer?.id, () => loadSessionInfo())
</script>

<template>
	<div class="peerSettingContainer w-100 h-100 position-absolute top-0 start-0 overflow-y-scroll ">
		<div class="d-flex h-100 w-100 pb-2">
			<div class="m-auto w-100 p-2">
				<div class="card rounded-3 shadow h-100" >
					<div class="card-header bg-transparent d-flex align-items-center gap-2 border-0 p-4 pb-2">
						<h4 class="mb-0 fw-normal">
							<LocaleText t="Peer Details"></LocaleText>
						</h4>
						<button type="button" class="btn-close ms-auto" @click="$emit('close')"></button>
					</div>
					<div class="card-body px-4">
						<div>
							<p class="mb-0 text-muted"><small>
								<LocaleText t="Peer"></LocaleText>
							</small></p>
							<h2>
								{{ selectedPeer.name }}
							</h2>
						</div>
						<div class="row mt-3 gy-2 gx-2 mb-2">
							<div class="col-12 col-lg-3">
								<div class="card rounded-3 bg-transparent h-100">
									<div class="card-body py-2 d-flex flex-column justify-content-center">
										<p class="mb-0 text-muted"><small>
											<LocaleText t="Status"></LocaleText>
										</small></p>
										<div class="d-flex align-items-center">
											<span class="dot ms-0 me-2" :class="{active: selectedPeer.status === 'running'}"></span>
											<LocaleText t="Connected" v-if="selectedPeer.status === 'running'"></LocaleText>
											<LocaleText t="Disconnected" v-else></LocaleText>
										</div>
									</div>
								</div>
							</div>
							<div class="col-12 col-lg-3">
								<div class="card rounded-3 bg-transparent  h-100">
									<div class="card-body py-2 d-flex flex-column justify-content-center">
										<p class="mb-0 text-muted"><small>
											<LocaleText t="Allowed IPs"></LocaleText>
										</small></p>
										{{selectedPeer.allowed_ip}}
									</div>
								</div>
							</div>
							<div style="word-break: break-all" class="col-12 col-lg-6">
								<div class="card rounded-3 bg-transparent h-100">
									<div class="card-body py-2 d-flex flex-column justify-content-center">
										<p class="mb-0 text-muted"><small>
											<LocaleText t="Public Key"></LocaleText>
										</small></p>
										<samp>{{selectedPeer.id}}</samp>
									</div>
								</div>
							</div>

                                                        <div class="col-12 col-lg-3">
                                                                <div class="card rounded-3 bg-transparent  h-100">
                                                                        <div class="card-body d-flex">
                                                                                <div>
                                                                                        <p class="mb-0 text-muted"><small>
                                                                                                <LocaleText t="Latest Handshake Time"></LocaleText>
                                                                                        </small></p>
                                                                                        <strong class="h4">
                                                                                                <LocaleText :t="selectedPeer.latest_handshake !== 'No Handshake' ? selectedPeer.latest_handshake + ' ago': 'No Handshake'"></LocaleText>
                                                                                        </strong>
                                                                                </div>
                                                                                <i class="bi bi-person-raised-hand ms-auto h2 text-muted"></i>
                                                                        </div>
                                                                </div>
                                                        </div>
                                                        <div class="col-12 col-lg-3">
                                                                <div class="card rounded-3 bg-transparent h-100">
                                                                        <div class="card-body d-flex flex-column justify-content-center">
                                                                                <p class="mb-0 text-muted"><small>
                                                                                        <LocaleText t="Active sessions"></LocaleText>
                                                                                </small></p>
                                                                                <div class="d-flex align-items-baseline gap-1">
                                                                                        <strong class="h4 text-info">{{limitInfo.activeSessions}}</strong>
                                                                                        <small class="text-muted" v-if="maxConcurrentDisplay">/ {{maxConcurrentDisplay}}</small>
                                                                                </div>
                                                                                <small class="text-muted"><LocaleText :t="policyLabel"></LocaleText></small>
                                                                        </div>
                                                                </div>
                                                        </div>
                                                        <div class="col-12 col-lg-3">
                                                                <div class="card rounded-3 bg-transparent  h-100">
                                                                        <div class="card-body d-flex">
                                                                                <div>
                                                                                        <p class="mb-0 text-muted"><small>
												<LocaleText t="Total Usage"></LocaleText>
											</small></p>
											<strong class="h4 text-warning">
												{{ (selectedPeer.total_data + selectedPeer.cumu_data).toFixed(4) }} GB
											</strong>
										</div>
										<i class="bi bi-arrow-down-up ms-auto h2 text-muted"></i>
									</div>
								</div>
							</div>
							<div class="col-12 col-lg-3">
								<div class="card rounded-3 bg-transparent  h-100">
									<div class="card-body d-flex">
										<div>
											<p class="mb-0 text-muted"><small>
												<LocaleText t="Total Received"></LocaleText>
											</small></p>
											<strong class="h4 text-primary">{{(selectedPeer.total_receive + selectedPeer.cumu_receive).toFixed(4)}} GB</strong>
										</div>
										<i class="bi bi-arrow-down ms-auto h2 text-muted"></i>
									</div>
								</div>
							</div>
							<div class="col-12 col-lg-3">
								<div class="card rounded-3 bg-transparent  h-100">
									<div class="card-body d-flex">
										<div>
											<p class="mb-0 text-muted"><small>
												<LocaleText t="Total Sent"></LocaleText>
											</small></p>
											<strong class="h4 text-success">{{(selectedPeer.total_sent + selectedPeer.cumu_sent).toFixed(4)}} GB</strong>
										</div>
										<i class="bi bi-arrow-up ms-auto h2 text-muted"></i>
									</div>
								</div>
							</div>
                                                        <div class="col-12">
                                                                <div class="card rounded-3 bg-transparent  h-100">
                                                                        <div class="card-header bg-transparent border-0 pb-0">
                                                                                <h5 class="mb-0"><LocaleText t="Current Sessions"></LocaleText></h5>
                                                                        </div>
                                                                        <div class="card-body pt-2">
                                                                                <p class="text-muted" v-if="usageSessions.length === 0">
                                                                                        <LocaleText t="No active sessions"></LocaleText>
                                                                                </p>
                                                                                <div class="d-flex flex-column gap-2" v-else>
                                                                                        <div class="border rounded-3 p-3" v-for="session in usageSessions" :key="session.endpoint">
                                                                                                <div class="d-flex justify-content-between align-items-baseline mb-1">
                                                                                                        <code>{{session.endpoint}}</code>
                                                                                                        <small class="text-muted">
                                                                                                                <LocaleText t="Last handshake"></LocaleText>:
                                                                                                                {{ formatAge(session.handshakeAgeSeconds) }}
                                                                                                        </small>
                                                                                                </div>
                                                                                                <div class="text-muted small">
                                                                                                        <LocaleText t="Traffic delta"></LocaleText>:
                                                                                                        ↑ {{ formatBytes(session.txDelta) }} / ↓ {{ formatBytes(session.rxDelta) }}
                                                                                                </div>
                                                                                                <div class="text-warning small" v-if="session.allowed === false">
                                                                                                        <LocaleText t="Awaiting slot"></LocaleText>
                                                                                                </div>
                                                                                        </div>
                                                                                </div>
                                                                        </div>
                                                                </div>
                                                        </div>
                                                        <div class="col-12">
                                                                <PeerTraffics
                                                                        :selectedDate="selectedDate"
                                                                        :selectedPeer="selectedPeer"></PeerTraffics>
                                                        </div>
							<div class="col-12">
								<PeerSessions
									:selectedDate="selectedDate"
									@selectDate="args => selectedDate = args"
									:selectedPeer="selectedPeer"></PeerSessions>
							</div>
							<div class="col-12">
								<PeerEndpoints
									:selectedPeer="selectedPeer"
								>
								</PeerEndpoints>
							</div>
						</div>


					</div>
				</div>
			</div>
		</div>
	</div>
</template>

<style scoped>

</style>